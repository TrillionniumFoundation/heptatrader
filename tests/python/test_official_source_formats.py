"""Execute the pinned parsers against synthetic provider formats, not text tokens."""
import copy
import unittest
from official_source_fixtures import extractor, payloads, OBSERVED
from hepta_strategy_contracts import digest_bytes

class OfficialSourceFormatTests(unittest.TestCase):
    def parse(self, url, data, observed=OBSERVED):
        digest=digest_bytes(data)
        if url==extractor.BLS_URL: return extractor._parse_bls(data,digest,observed)
        if url==extractor.FED_CALENDAR_URL: return extractor._parse_fed_calendar(data,digest,observed)
        if url==extractor.ECB_CALENDAR_URL: return extractor._parse_ecb_calendar(data,digest,observed)
        return extractor._parse_rss(data,source_digest=digest,fetched_at_ms=observed,role='fed-press' if url==extractor.FED_PRESS_URL else 'ecb-press')

    def test_all_five_pinned_formats_produce_economic_evidence(self):
        for url,data in payloads().items():
            with self.subTest(source=url):
                result=self.parse(url,data)
                self.assertTrue(result.get('completeness') or result['items'])

    def test_format_identity_and_future_evidence_are_rejected(self):
        for url,data in payloads().items():
            with self.subTest(source=url):
                with self.assertRaises(extractor.ExtractionError): self.parse(url,b'<invalid/>')
                with self.assertRaises(extractor.ExtractionError): self.parse(url,data,0)

    def test_partial_calendars_and_duplicate_rss_are_not_complete(self):
        data=payloads()
        with self.assertRaises(extractor.ExtractionError):
            self.parse(extractor.FED_CALENDAR_URL,data[extractor.FED_CALENDAR_URL].replace(b'January',b'Unknown'))
        with self.assertRaises(extractor.ExtractionError):
            self.parse(extractor.BLS_URL,data[extractor.BLS_URL].replace(b'TZID:US-Eastern',b'TZID:UTC'))
        rss=data[extractor.FED_PRESS_URL]; start=rss.index(b'<item>');end=rss.index(b'</item>')+7
        with self.assertRaises(extractor.ExtractionError): self.parse(extractor.FED_PRESS_URL,rss[:end]+rss[start:end]+rss[end:])


class EvidenceCoverageTimeTests(unittest.TestCase):
    def sources(self):
        return [dict(provider=provider,source_ref=url,retrieved_at_ms=OBSERVED,published_at_ms=OBSERVED-1000,
            revision='fixture',content_sha256='sha256:'+str(index)*64,coverage_start_ms=OBSERVED-1000,
            coverage_end_ms=OBSERVED,currencies=[currency]) for index,(provider,url,currency) in enumerate((
            ('FEDERAL_RESERVE',extractor.FED_PRESS_URL,'USD'),('ECB',extractor.ECB_PRESS_URL,'EUR')),1)]

    def test_known_at_fetch_is_valid_after_fetch_but_cannot_claim_future_news(self):
        import hepta_market_context_builder as builder
        from hepta_strategy_contracts import ContractError
        sources=self.sources()
        normalized,_=builder._provenance_sources(sources,OBSERVED+6,'invalid-source')
        self.assertEqual(normalized[0]['coverage_end_ms'],OBSERVED)
        sources[0]['coverage_end_ms']=OBSERVED+6
        with self.assertRaises(ContractError): builder._provenance_sources(sources,OBSERVED+6,'invalid-source')

    def test_calendar_still_needs_future_schedule_and_sources_cannot_be_from_future(self):
        import hepta_market_context_builder as builder
        from hepta_strategy_contracts import ContractError
        sources=self.sources();sources[0]['source_ref']=extractor.FED_CALENDAR_URL
        with self.assertRaises(ContractError): builder._provenance_sources(sources,OBSERVED+6,'invalid-source')
        sources[0]['coverage_end_ms']=OBSERVED+10000
        builder._provenance_sources(sources,OBSERVED+6,'invalid-source')
        sources[1]['retrieved_at_ms']=OBSERVED+7
        with self.assertRaises(ContractError): builder._provenance_sources(sources,OBSERVED+6,'invalid-source')

    def test_stale_press_evidence_remains_absent_even_with_valid_historical_coverage(self):
        import hepta_market_context_builder as builder
        import hepta_eurusd_confirmed_momentum_strategy as strategy
        from hepta_strategy_contracts import digest_document
        config=strategy.load_strategy(builder.Path(__file__).resolve().parents[2]/'strategies/eurusd-confirmed-momentum-shadow-v2.json')
        body=dict(schema='hepta.market-information-items.v2',provider='fixture',source_ref='fixture',observed_at_ms=OBSERVED,
            sources=self.sources(),items=[])
        # Use the exact public v2 shape; v3 attestation is separately exercised
        # by the actual root-owned capture integration, not mocked here.
        self.assertEqual(set(body)|{'body_sha256'},builder.INFORMATION_V2_FIELDS)
        document={**body,'body_sha256':digest_document(body)}
        fresh=builder._information(document,digest_document(document),config,OBSERVED+1)
        old=builder._information(document,digest_document(document),config,OBSERVED+900001)
        self.assertTrue(fresh['present']);self.assertFalse(old['present'])

if __name__=='__main__': unittest.main()
