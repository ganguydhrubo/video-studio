import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from video_studio import briefing, editor, story, studio
from video_studio.util import ToolError


class BriefIntent(unittest.TestCase):
    def test_real_brief_does_not_turn_hook_into_video_duration(self):
        intent = briefing.local_intent('best taliking head wit 2 sec hook ')
        self.assertIsNone(intent['target_seconds'])
        self.assertEqual(intent['hook_seconds'], 2)
        self.assertTrue(intent['preserve_content'])

    def test_separate_total_and_component_times(self):
        for brief in ('45-second video with a 2 sec hook', 'hook for 2 seconds; make a 45 second reel'):
            intent = briefing.local_intent(brief)
            self.assertEqual((intent['target_seconds'], intent['hook_seconds']), (45, 2))
        for brief in ('1 second transition', 'outro: 3 seconds', 'remove 2 seconds from the start'):
            self.assertIsNone(briefing.local_intent(brief)['target_seconds'])
        self.assertEqual(briefing.local_intent('a 2 min youtube video')['target_seconds'], 120)
        self.assertFalse(briefing.local_intent('make it shorter')['preserve_content'])

    def test_ai_corrects_wording_without_overriding_validated_duration(self):
        answer = {'normalized_brief': 'Best talking head with a 2-second hook.',
                  'target_seconds': 2, 'suggestions': ['Add readable captions'], 'questions': []}
        with patch('video_studio.briefing.Director') as director:
            director.return_value.ask_json.return_value = (answer, 'gemini')
            result = briefing.interpret('best taliking head wit 2 sec hook')
        self.assertEqual(result['source'], 'ai:gemini')
        self.assertIn('talking head', result['normalized_brief'])
        self.assertIsNone(result['target_seconds'])
        self.assertTrue(result['preserve_content'])

    def test_ai_failure_and_off_preserve_original(self):
        with patch('video_studio.briefing.Director') as director:
            director.return_value.ask_json.side_effect = ToolError('unavailable')
            result = briefing.interpret('best taliking head wit 2 sec hook')
            self.assertTrue(result['warnings'])
            self.assertTrue(result['preserve_content'])
        with patch('video_studio.briefing.Director', side_effect=AssertionError('must stay offline')):
            self.assertEqual(briefing.interpret('my video', preferred='none')['source'], 'local')

    def test_full_message_and_ai_guard(self):
        utts = [{'id':f'u{i}', 'start':i*7, 'end':i*7+7, 'text':f'Complete sentence {i}.'} for i in range(9)]
        keep, _ = story.build(utts, set(), [], {}, None, preserve_content=True)
        self.assertEqual(keep, [u['id'] for u in utts])
        proposed, notes = story.accept_review({'keep': ['u0','u6']}, utts, set(), {}, {}, keep, None, preserve_content=True)
        self.assertIsNone(proposed)
        self.assertTrue(notes)

    def test_slow_ai_is_shared_and_does_not_block_http(self):
        event = threading.Event()
        brief = 'Unique test brief for asynchronous assistance'
        def slow(*args):
            event.wait(2)
            return briefing.local_intent(brief)
        studio.BRIEF_RESULTS.clear()
        with patch('video_studio.briefing.interpret', side_effect=slow) as call:
            start = time.monotonic()
            first = studio.brief_assistance({'brief':brief, 'provider':'gemini'})
            second = studio.brief_assistance({'brief':brief, 'provider':'gemini'})
            self.assertLess(time.monotonic()-start, .25)
            self.assertTrue(first['checking'])
            self.assertEqual(first['request_id'],second['request_id'])
            event.set()
            for _ in range(100):
                if not studio.BRIEF_RESULTS[first['request_id']]['checking']:
                    break
                time.sleep(.005)
            self.assertEqual(call.call_count, 1)
            self.assertFalse(studio.BRIEF_RESULTS[first['request_id']]['checking'])

    def test_full_message_keeps_spoken_fillers_and_does_not_repeat_boundaries(self):
        from video_studio.project import Project
        with tempfile.TemporaryDirectory() as folder:
            project = Project(Path(folder))
            project.save({'id':'test', 'edit_options':{'keep_silences':False}, 'settings':{'pacing':'auto'}})
            words = [{'text':'Actually', 'start':0, 'end':.5}, {'text':'yes.', 'start':.5, 'end':1},
                     {'text':'Continue', 'start':1, 'end':1.5}, {'text':'now.', 'start':1.5, 'end':2}]
            plan = {'keep':['a','b'], 'utterances':[{'id':'a','start':0,'end':1,'text':'Actually yes.'}, {'id':'b','start':1,'end':2,'text':'Continue now.'}],
                    'coverage':{'preserve_content':True}}
            pieces = editor._pieces(project, plan, words)
            self.assertEqual(sum(len(p['words']) for p in pieces), 4)
            self.assertLessEqual(pieces[0]['end'], pieces[1]['start'])


class BriefEdgeCases(unittest.TestCase):
    def intent(self, brief):
        r = briefing.local_intent(brief)
        return r['target_seconds'], r['hook_seconds'], r['preserve_content']

    def test_typos_spelled_numbers_and_indian_languages(self):
        self.assertEqual(self.intent('talking head with 2 sec hok'), (None, 2, True))
        self.assertEqual(self.intent('intor 3s then the talk'), (None, 3, True))
        self.assertEqual(self.intent('two sec hook, keep everything'), (None, 2, True))
        self.assertEqual(self.intent('2 सेकंड का हुक'), (None, 2, True))
        self.assertEqual(self.intent('২ সেকেন্ডের হুক দিয়ে ভিডিও'), (None, 2, True))
        self.assertEqual(self.intent('১ মিনিটের ভিডিও'), (60, None, False))
        self.assertEqual(self.intent('1 मिनट का वीडियो'), (60, None, False))
        self.assertEqual(self.intent('someone said 5 things'), (None, None, True))

    def test_explicit_totals_still_work(self):
        self.assertEqual(self.intent('keep it under 30 sec')[0], 30)
        self.assertEqual(self.intent('trim it to 5 sec')[0], 5)
        self.assertEqual(self.intent('Best talking-head video with a 2-second hook'), (None, 2, True))
        self.assertIsNone(self.intent('cut the first 3 seconds')[0])

    def test_unclear_short_time_keeps_everything_and_asks(self):
        r = briefing.local_intent('make a 3 sec')
        self.assertIsNone(r['target_seconds'])
        self.assertTrue(r['preserve_content'])
        self.assertTrue(r['questions'])

    def test_ai_explanation_added_to_wording_is_not_used(self):
        # Real answer seen from a free model on 16 Nov 2026: the padding would read as a 2-second video.
        answer = {'normalized_brief': 'Best talking-head video with a 2-second hook. Overall video duration is unspecified; '
                                      'the 2 seconds applies only to the hook, not the full video. Keep all existing content intact.',
                  'summary': 'x', 'suggestions': [], 'questions': []}
        with patch('video_studio.briefing.Director') as director:
            director.return_value.ask_json.return_value = (answer, 'opencode')
            result = briefing.interpret('best taliking head wit 2 sec hook')
        self.assertEqual(result['normalized_brief'], 'best taliking head wit 2 sec hook')
        self.assertIsNone(result['target_seconds'])
        self.assertTrue(result['warnings'])


class ProviderFailures(unittest.TestCase):
    def test_refused_account_is_tried_last_and_explained(self):
        from video_studio import ai
        with tempfile.TemporaryDirectory() as home, patch.dict('os.environ', {'VIDEO_STUDIO_HOME': home}):
            ai._record_health('gemini', ai._short_error('Google Gemini CLI: ' + ai.ACCOUNT_REFUSED))
            with patch('video_studio.ai.config.get', side_effect=lambda k: {
                    'ai.provider': 'gemini', 'ai.allow_fallback': True, 'ai.paid_fallback': False,
                    'ai.fallback': ['opencode', 'gemini'], 'ai.max_calls_per_project': 12}[k]):
                order = [p.key for p in ai.Director('gemini')._candidates()]
            self.assertEqual(order, ['opencode', 'gemini'])
            self.assertTrue(ai._account_refused('at throwIneligibleOrProjectIdError https://antigravity.google'))
            self.assertIn('not eligible', ai._short_error('x\nGoogle Gemini CLI: ' + ai.ACCOUNT_REFUSED))
