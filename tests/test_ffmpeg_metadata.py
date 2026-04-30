import unittest

from automakeclip.ffmpeg import _extract_kill_events, _parse_steelseries_metadata


class FFmpegMetadataTests(unittest.TestCase):
    def test_reassembles_steelseries_json_and_kill_events(self) -> None:
        tags = {
            "STEELSERIES_META0000": "{\"last_game_name\":\"Overwatch\",\"gamesense_events\":[",
            "STEELSERIES_META0001": "{\"type\":\"KILL\",\"clip_timestamp\":9.25,\"unlocalized_display_name\":\"ELIMINATION\",\"previewable\":true},",
            "STEELSERIES_META0002": "{\"type\":\"KILL\",\"clip_timestamp\":11.75,\"unlocalized_display_name\":\"DOUBLE ELIMINATION\",\"previewable\":true}]}",
        }

        payload = _parse_steelseries_metadata(tags)
        events = _extract_kill_events(payload)

        self.assertEqual(payload["last_game_name"], "Overwatch")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1]["name"], "DOUBLE ELIMINATION")
        self.assertAlmostEqual(events[0]["timestamp"], 9.25)


if __name__ == "__main__":
    unittest.main()
