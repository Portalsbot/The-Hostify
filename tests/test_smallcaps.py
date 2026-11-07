import unittest

from smallcaps import sc


class SmallCapsTests(unittest.TestCase):
    def test_prose_is_converted(self):
        self.assertEqual(sc("Hello World"), "ʜᴇʟʟᴏ ᴡᴏʀʟᴅ")

    def test_telegram_identifiers_are_preserved(self):
        source = "Follow @Anime_Portals and use /help or /setbrand."
        rendered = sc(source)
        self.assertIn("@Anime_Portals", rendered)
        self.assertIn("/help", rendered)
        self.assertIn("/setbrand", rendered)

    def test_urls_environment_keys_and_template_fields_are_preserved(self):
        source = "BOT_TOKEN=secret https://t.me/Anime_portals {developer_id}"
        rendered = sc(source)
        self.assertIn("BOT_TOKEN=", rendered)
        self.assertIn("https://t.me/Anime_portals", rendered)
        self.assertIn("{developer_id}", rendered)
        self.assertIn("ꜱᴇᴄʀᴇᴛ", rendered)


    def test_uppercase_labels_are_also_small_caps(self):
        self.assertEqual(sc("CPU RAM PID ID"), "ᴄᴘᴜ ʀᴀᴍ ᴘɪᴅ ɪᴅ")

    def test_message_keys_and_snake_case_fields_are_preserved(self):
        source = "M03_OWNER_WELCOME promo_channel_username"
        self.assertEqual(sc(source), source)


if __name__ == "__main__":
    unittest.main()
