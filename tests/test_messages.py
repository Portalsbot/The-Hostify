import re
import string
import unittest

import messages as M


class MessageTemplateTests(unittest.TestCase):
    def test_every_registered_template_is_named_and_nonempty(self):
        self.assertGreater(len(M.DEFAULT_MESSAGES), 80)
        self.assertTrue(all(re.fullmatch(r"M\d{2,3}_[A-Z0-9_]+", key) and value for key, value in M.DEFAULT_MESSAGES.items()))

    def test_deployment_announcement_has_arguments_for_every_placeholder(self):
        names = {name for _, name, _, _ in string.Formatter().parse(M.M29_CHANNEL_DEPLOY) if name}
        values = {
            "bot_mention", "owner_mention", "repo_url", "env_lines", "brand_name",
            "developer_mention", "developer_id", "support_mention", "support_id",
            "promo_channel_mention", "promo_channel_url", "promo_channel_id",
        }
        self.assertEqual(names, values)

    def test_promotion_and_support_fields_are_present(self):
        for field in ("{developer_mention}", "{developer_id}", "{support_mention}",
                      "{support_id}", "{promo_channel_mention}", "{promo_channel_url}",
                      "{promo_channel_id}"):
            self.assertIn(field, M.M29_CHANNEL_DEPLOY)


if __name__ == "__main__":
    unittest.main()
