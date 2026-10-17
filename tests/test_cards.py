import os
import struct
import tempfile
import unittest

import cards
from tests.test_post_composer import BrowserTestCase

SPEC = {"headline": "5 mẹo học từ vựng", "subline": "Nhớ lâu hơn mỗi ngày", "points": ["Học theo chủ đề", "Ôn sau 1 ngày", "Dùng ngay khi nói"],
        "footer": "#HiEnglish", "theme": "ocean"}


def png_size(path):
    with open(path, "rb") as handle:
        header = handle.read(24)
    assert header[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", header[16:24])


class StandaloneRenderTest(unittest.TestCase):
    def test_without_a_browser_one_is_opened(self):
        with tempfile.TemporaryDirectory() as folder:
            out = os.path.join(folder, "the.png")
            cards.render_card(SPEC, out, size=300)
            self.assertEqual(png_size(out), (300, 300))


class HtmlTest(unittest.TestCase):
    def test_the_text_is_on_the_page(self):
        page = cards.build_html(SPEC)
        for text in ("5 mẹo học từ vựng", "Nhớ lâu hơn mỗi ngày", "Học theo chủ đề", "#HiEnglish"):
            self.assertIn(text, page)

    def test_text_cannot_become_markup(self):
        page = cards.build_html({"headline": "<script>alert(1)</script>", "points": ['<img src=x onerror="y">'], "footer": "a&b"})
        self.assertNotIn("<script>alert(1)", page)
        self.assertNotIn("<img", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)
        self.assertIn("a&amp;b", page)

    def test_an_unknown_theme_uses_the_default(self):
        self.assertIn(cards.THEMES[cards.DEFAULT_THEME][0], cards.build_html({"headline": "x", "theme": "<style>"}))

    def test_empty_parts_are_left_out(self):
        page = cards.build_html({"headline": "Chỉ tiêu đề", "subline": " ", "points": ["", " "], "footer": ""})
        self.assertNotIn('class="sub"', page)
        self.assertNotIn("<li>", page)
        self.assertNotIn('class="footer"', page)

    def test_every_theme_has_three_colours(self):
        for name, colours in cards.THEMES.items():
            self.assertEqual(len(colours), 3, name)


class RenderTest(BrowserTestCase):
    def test_a_square_png_is_written(self):
        with tempfile.TemporaryDirectory() as folder:
            out = os.path.join(folder, "con", "the.png")
            self.assertEqual(cards.render_card(SPEC, out, size=600, browser=self.browser), out)
            self.assertEqual(png_size(out), (600, 600))
            self.assertGreater(os.path.getsize(out), 2000)

    def test_every_theme_draws(self):
        with tempfile.TemporaryDirectory() as folder:
            for theme in cards.THEMES:
                out = os.path.join(folder, f"{theme}.png")
                cards.render_card({**SPEC, "theme": theme}, out, size=400, browser=self.browser)
                self.assertEqual(png_size(out), (400, 400), theme)

    def test_long_text_is_shrunk_to_fit(self):
        spec = {"headline": "Một tiêu đề rất dài " * 6, "subline": "phụ đề dài " * 12, "points": ["một ý khá dài để thử " * 4] * 4,
                "footer": "#chan", "theme": "night"}
        self.page.set_viewport_size({"width": 600, "height": 600})
        self.page.set_content(cards.build_html(spec, 600))
        fits = self.page.evaluate(
            """() => { const card = document.getElementById('card'); let h = 0;
                for (const el of card.children) { if (!el.classList.contains('footer')) h += el.getBoundingClientRect().height; }
                return h <= 600 - 60 - 75 + 40 + 1; }"""
        )
        self.assertTrue(fits)
        size = self.page.evaluate("parseFloat(document.getElementById('headline').style.fontSize || 0)")
        self.assertLess(size, 600 // 11)

    def test_short_text_keeps_the_full_size(self):
        self.page.set_content(cards.build_html({"headline": "Ngắn", "theme": "paper"}, 600))
        self.assertEqual(self.page.evaluate("document.getElementById('headline').style.fontSize"), "")


if __name__ == "__main__":
    unittest.main()
