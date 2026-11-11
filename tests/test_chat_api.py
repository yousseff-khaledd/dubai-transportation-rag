"""Day 6 checks with real graphs/checkpoints and deterministic model substitutes."""
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.middleware.tool_call_limit import ToolCallLimitExceededError
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.exceptions import OutputParserException

from app.agents.chat import ChatConflict, ChatRequest, ResumeRequest, draft_answer, run_chat
from app.main import app
from app.rag.search import SearchUnavailable


def ambiguous_plan(state):
    return {'plan': {'route': 'needs_clarification', 'search_query': '', 'clarification': 'Which vehicle?'}}


def outside_plan(state):
    return {'plan': {'route': 'out_of_scope', 'search_query': '', 'clarification': None}}


class ToolCallingModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


class ChatApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.database = Path(self.directory.name) / 'checkpoints.sqlite'
        checkpoint_patch = patch('app.agents.chat.CHECKPOINT_PATH', self.database)
        checkpoint_patch.start()
        self.addCleanup(checkpoint_patch.stop)
        self.client = TestClient(app)

    def assert_envelope(self, response, status_code, status):
        self.assertEqual(response.status_code, status_code, response.text)
        body = response.json()
        self.assertTrue({'thread_id', 'request_id', 'status', 'answer', 'citations', 'clarification'} <= body.keys())
        self.assertEqual(body['status'], status)
        self.assertEqual(body['request_id'], response.headers['X-Request-ID'])
        return body

    def test_pending_interrupt_checks_and_resume(self):
        with patch('app.agents.chat.question_guard', side_effect=ambiguous_plan):
            paused = self.assert_envelope(self.client.post('/chat', json={
                'thread_id': 'a', 'message': 'What is its fee?', 'mode': 'lexical', 'filters': {'year': 2023}}),
                200, 'needs_clarification')
        self.assertEqual(paused['clarification'], 'Which vehicle?')
        resume = {'thread_id': 'a', 'message': 'autonomous vehicles', 'interrupt_id': paused['interrupt_id']}
        for invalid_resume in [dict(resume, thread_id='b'), dict(resume, interrupt_id='wrong')]:
            body = self.assert_envelope(self.client.post('/chat/resume', json=invalid_resume), 409, 'error')
            self.assertEqual(body['error']['code'], 'invalid_resume')
        pending = self.assert_envelope(self.client.post('/chat', json={'thread_id': 'a', 'message': 'overwrite'}), 409, 'error')
        self.assertEqual(pending['error']['code'], 'clarification_pending')

        seen = []
        def resolve_plan(state):
            seen.append(state)
            return outside_plan(state)
        with patch('app.agents.chat.question_guard', side_effect=resolve_plan):
            completed = self.assert_envelope(self.client.post('/chat/resume', json=resume), 200, 'out_of_scope')
        self.assertNotEqual(completed['request_id'], paused['request_id'])
        self.assertIsNone(completed['interrupt_id'])
        self.assertEqual(seen[0]['request']['mode'], 'lexical')
        self.assertEqual(seen[0]['request']['filters']['year'], 2023)
        self.assertEqual(seen[0]['clarifications'], [{'question': 'Which vehicle?', 'answer': 'autonomous vehicles'}])
        self.assert_envelope(self.client.post('/chat/resume', json=resume), 409, 'error')

    def test_two_attempt_cap_and_new_question_reset(self):
        with patch('app.agents.chat.question_guard', side_effect=ambiguous_plan):
            first = run_chat(ChatRequest(thread_id='a', message='Its fee?'))
            second = run_chat(ResumeRequest(thread_id='a', message='that one', interrupt_id=first.interrupt_id))
            self.assertEqual(second.status, 'needs_clarification')
            self.assertNotEqual(first.interrupt_id, second.interrupt_id)
            with self.assertRaises(ChatConflict):
                run_chat(ResumeRequest(thread_id='a', message='old answer', interrupt_id=first.interrupt_id))
            final = run_chat(ResumeRequest(thread_id='a', message='still unclear', interrupt_id=second.interrupt_id))
            self.assertEqual(final.status, 'insufficient_evidence')
            self.assertIsNone(final.answer)
            self.assertIsNone(final.interrupt_id)
            new_question = run_chat(ChatRequest(thread_id='a', message='Another unclear question'))
            self.assertEqual(new_question.status, 'needs_clarification')

    def test_resume_after_process_restart(self):
        # Separate interpreters prove there is no dependency on in-memory state.
        script = '''
import json, sys
from pathlib import Path
from unittest.mock import patch
from app.agents.chat import ChatRequest, ResumeRequest, run_chat
def plan(state):
    clarified = bool(state['clarifications'])
    return {'plan': {'route': 'out_of_scope' if clarified else 'needs_clarification',
                     'search_query': '', 'clarification': 'Which vehicle?'}}
body = json.loads(sys.argv[2])
request = ResumeRequest(**body) if 'interrupt_id' in body else ChatRequest(**body)
with patch('app.agents.chat.CHECKPOINT_PATH', Path(sys.argv[1])), patch('app.agents.chat.question_guard', side_effect=plan):
    print(run_chat(request).model_dump_json())
'''
        def separate_process(body):
            completed = subprocess.run([sys.executable, '-c', script, str(self.database), json.dumps(body)],
                                       capture_output=True, text=True, check=True, timeout=30)
            return json.loads(completed.stdout)
        paused = separate_process({'thread_id': 'restart', 'message': 'Which permit?'})
        resumed = separate_process({'thread_id': 'restart', 'message': 'a fictional vehicle',
                                    'interrupt_id': paused['interrupt_id']})
        self.assertEqual(resumed['status'], 'out_of_scope')

    def test_same_thread_rejected_while_other_thread_runs(self):
        started, release = Event(), Event()
        def blocking_plan(state):
            if state['request']['thread_id'] == 'a':
                started.set()
                if not release.wait(timeout=10):
                    raise TimeoutError('test did not release thread')
            return outside_plan(state)
        with patch('app.agents.chat.question_guard', side_effect=blocking_plan), ThreadPoolExecutor(max_workers=1) as pool:
            running = pool.submit(run_chat, ChatRequest(thread_id='a', message='first'))
            try:
                self.assertTrue(started.wait(timeout=5))
                busy = self.assert_envelope(self.client.post('/chat', json={'thread_id': 'a', 'message': 'overlap'}), 409, 'error')
                self.assertEqual(busy['error']['code'], 'thread_busy')
                self.assertEqual(run_chat(ChatRequest(thread_id='b', message='independent')).status, 'out_of_scope')
            finally:
                release.set()
            self.assertEqual(running.result(timeout=5).status, 'out_of_scope')

    def test_validation_and_failure_envelopes(self):
        for body in [{'thread_id': 'a', 'message': 'x', 'filters': {'year': '2023'}},
                     {'thread_id': 'a', 'message': 'x', 'filters': {'type': 'Other'}},
                     {'thread_id': 'a', 'message': 'x', 'mode': 'unknown'},
                     {'thread_id': 'a', 'message': 'x' * 2001},
                     {'thread_id': 'a', 'message': '  '},
                     {'thread_id': 'a', 'message': 'x', 'top_k': 21},
                     {'thread_id': '../a', 'message': 'x'}]:
            self.assert_envelope(self.client.post('/chat', json=body), 422, 'error')
        malformed = self.assert_envelope(self.client.post('/chat', content='{bad',
                                        headers={'Content-Type': 'application/json'}), 422, 'error')
        self.assertIsNone(malformed['thread_id'])
        self.assert_envelope(self.client.post('/chat/resume', json={'thread_id': 'a', 'message': 'x'}), 422, 'error')
        for exception, expected_status, code in [(SearchUnavailable('missing'), 503, 'search_unavailable'),
                                                (sqlite3.OperationalError('locked'), 503, 'storage_unavailable'),
                                                (OutputParserException('invalid output'), 502, 'model_failure'),
                                                (TimeoutError(), 504, 'model_timeout'),
                                                (RuntimeError('private detail'), 500, 'internal_error')]:
            with patch('app.api.app.run_chat', side_effect=exception):
                body = self.assert_envelope(self.client.post('/chat', json={'thread_id': 'a', 'message': 'x'}),
                                           expected_status, 'error')
                self.assertEqual(body['error']['code'], code)
                self.assertNotIn('private detail', json.dumps(body))

    def test_failure_releases_thread(self):
        with patch('app.agents.chat.question_guard', side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                run_chat(ChatRequest(thread_id='a', message='first'))
        with patch('app.agents.chat.question_guard', side_effect=outside_plan):
            self.assertEqual(run_chat(ChatRequest(thread_id='a', message='retry')).status, 'out_of_scope')

    def test_answer_agent_call_limits(self):
        evidence = {'chunk_id': 'chunk', 'document_id': 'doc', 'locator': '1', 'title': 'Test', 'text': 'Test',
                    'source_url': '', 'content_url': '', 'primary_category': 'Roads and Transport',
                    'year': 2023, 'type': 'Law', 'status': None, 'article_number': None, 'score': 1.0}
        state = {'evidence': [evidence], 'plan': {'search_query': 'vehicle permit'}}
        # Invalid locators never open the database. The model repeatedly calls the tool.
        for batch_size, expected_exception in [(1, ModelCallLimitExceededError), (5, ToolCallLimitExceededError)]:
            calls = [{'name': 'get_document_excerpt', 'args': {'document_id': 'other', 'locator': '1'},
                      'id': str(index)} for index in range(batch_size)]
            model = ToolCallingModel(responses=[AIMessage(content='', tool_calls=calls)])
            with patch('app.agents.answer_agent.chat_model', return_value=model), self.assertRaises(expected_exception):
                draft_answer(state)


if __name__ == '__main__':
    unittest.main()
