"""Source-bound, opt-in technical table pagination regression.

A fitting synthetic row is an indivisible presentation unit. Native content
is compared with an independently prepared typed source; no scientific or
accessibility claims follow from this PDF geometry test.
"""
from __future__ import annotations

from hashlib import sha256
from unittest import TestCase

from reporting.pdf_generated_source_job import render_pdf_candidate
from reporting.pdf_typed_observation import observe_typed_pdf
from reporting.typed_html import prepare_typed_html
from reporting.typed_pdf_association import associate_typed_pdf
from reporting.typed_pdf_candidate import _render_html
from reporting.typed_pdf_source import prepare_typed_pdf_source
from test_reporting_typed_pdf_source import digest,document


class TypedTableContinuationTests(TestCase):
    """Fixed owned-synthetic input and source-bound native output."""

    @staticmethod
    def source():
        cell=' '.join(f'C{i:03d}' for i in range(100))
        return document(24,paragraph='Owned synthetic source-bound table continuation test.',
                        labels=('Column','Column'),cells=(cell,cell))

    def test_a_exact_wrapper_preserves_table_source_and_declares_keep_row(self):
        source=self.source()
        typed,_=prepare_typed_html(presentation_ir=source,
                                   expected_ir_sha256=digest(source))
        html=_render_html(typed)
        typed_body=typed.split('<body>',1)[1].split('</body>',1)[0]
        actual_body=html.split('<body>',1)[1].split('</body>',1)[0]
        self.assertEqual(actual_body,typed_body)
        self.assertIn('thead{display:table-header-group}',html)
        self.assertIn('tbody tr:not(:only-child){break-inside:avoid;page-break-inside:avoid}',html)
        self.assertIn('h2,caption{break-after:avoid;page-break-after:avoid}',html)
        self.assertNotIn('display:none',html)
        self.assertNotIn('position:absolute',html)

    def test_b_real_source_bound_pdf_keeps_fitting_table_rows_together(self):
        source=self.source()
        source_sha=digest(source)
        typed,_=prepare_typed_html(presentation_ir=source,
                                   expected_ir_sha256=source_sha)
        html=_render_html(typed)
        rendered=render_pdf_candidate(html,sha256(html.encode('utf-8')).hexdigest())
        observed=observe_typed_pdf(pdf_bytes=rendered.pdf_bytes,
                                   expected_pdf_sha256=rendered.pdf_sha256)
        expected=prepare_typed_pdf_source(presentation_ir=source,
                                          expected_ir_sha256=source_sha)
        assoc=associate_typed_pdf(presentation_ir=source,
            expected_ir_sha256=source_sha,
            expected_source_expectation_sha256=expected.expectation_sha256,
            observation=observed,
            expected_observation_sha256=sha256(observed.to_bytes()).hexdigest(),
            expected_pdf_sha256=rendered.pdf_sha256)
        result=assoc.to_dict()
        self.assertEqual(result['source_counts']['table_rows'],24)
        self.assertEqual(len(result['row_segments']),24)
        self.assertEqual([row['row_ordinal'] for row in result['row_segments']],
                         list(range(24)))
        self.assertEqual({row['segment_ordinal'] for row in result['row_segments']},{0})
        self.assertEqual(result['page_count'],rendered.page_count)
        self.assertLessEqual(result['page_count'],64)
        self.assertIs(result['claims']['literal_hashes_matched'],True)
        self.assertIs(result['claims']['release_authorized'],False)


if __name__=='__main__':
    import unittest
    unittest.main()
