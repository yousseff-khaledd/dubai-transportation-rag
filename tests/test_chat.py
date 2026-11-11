import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.chat import ChatRequest, run_chat, response_guard


class ChatTests(unittest.TestCase):
    def test_invalid_citation_abstains(self):
        state = {
            'request': ChatRequest(thread_id='a', message='vehicle permit').model_dump(),
            'request_id': 'request', 'plan': {'route': 'retrieve', 'search_query': 'vehicle permit', 'clarification': None},
            'evidence': [{'chunk_id': 'one', 'document_id': 'doc', 'locator': '2'}],
            'draft': {'status': 'answered', 'answer': 'A permit is needed.',
                      'citations': [{'chunk_id': 'wrong', 'document_id': 'doc', 'locator': '2'}]},
        }
        result = response_guard(state)['response']
        self.assertEqual(result['status'], 'insufficient_evidence')
        self.assertIsNone(result['answer'])
        self.assertEqual(result['citations'], [])

    def test_thread_history_and_per_question_state(self):
        seen = []
        def fake_plan(state):
            seen.append((state['request']['thread_id'], list(state.get('history', [])), state['evidence'], state['draft']))
            return {'plan': {'route': 'out_of_scope', 'search_query': state['request']['message'], 'clarification': None}}
        with tempfile.TemporaryDirectory() as directory, \
                patch('app.agents.chat.CHECKPOINT_PATH', Path(directory) / 'checkpoints.sqlite'), \
                patch('app.agents.chat.question_guard', side_effect=fake_plan):
            run_chat(ChatRequest(thread_id='a', message='first'))
            run_chat(ChatRequest(thread_id='a', message='follow up'))
            run_chat(ChatRequest(thread_id='b', message='follow up'))
        self.assertEqual(len(seen[1][1]), 1)
        self.assertEqual(seen[1][1][0]['question'], 'first')
        self.assertEqual(seen[2][1], [])
        for _, _, evidence, draft in seen:
            self.assertEqual(evidence, [])
            self.assertEqual(draft, {})

    def test_invalid_thread_id(self):
        with self.assertRaises(ValueError):
            ChatRequest(thread_id='../other', message='question')


if __name__ == '__main__':
    unittest.main()
