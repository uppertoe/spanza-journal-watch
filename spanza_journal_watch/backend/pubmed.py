import calendar
import datetime
import json
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


class PubmedAPIError(Exception):
    pass


class PubmedClient:
    BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def __init__(self, api_key="", timeout=20):
        self.api_key = (api_key or "").strip()
        self.timeout = timeout

    def _request_json(self, endpoint, params):
        query = dict(params or {})
        if self.api_key:
            query["api_key"] = self.api_key
        url = f"{self.BASE_URL}/{endpoint}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url=url, headers={"User-Agent": "spanza-journal-watch/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
        except Exception as error:
            raise PubmedAPIError(f"PubMed request failed: {error}") from error

        try:
            return json.loads(payload)
        except json.JSONDecodeError as error:
            raise PubmedAPIError(f"PubMed returned invalid JSON: {error}") from error

    def _request_xml(self, endpoint, params):
        query = dict(params or {})
        if self.api_key:
            query["api_key"] = self.api_key
        url = f"{self.BASE_URL}/{endpoint}?{urllib.parse.urlencode(query)}"
        request = urllib.request.Request(url=url, headers={"User-Agent": "spanza-journal-watch/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
        except Exception as error:
            raise PubmedAPIError(f"PubMed request failed: {error}") from error

        try:
            return ET.fromstring(payload)
        except ET.ParseError as error:
            raise PubmedAPIError(f"PubMed returned invalid XML: {error}") from error

    def ping(self):
        data = self._request_json("einfo.fcgi", {"db": "pubmed", "retmode": "json"})
        if not data.get("einforesult"):
            raise PubmedAPIError("Could not validate PubMed API key.")

    def search_journals(self, query, retmax=20):
        term = (query or "").strip()
        if not term:
            return []

        data = self._request_json(
            "esearch.fcgi",
            {
                "db": "nlmcatalog",
                "retmode": "json",
                "retmax": retmax,
                "term": f"{term}[Journal]",
            },
        )
        ids = data.get("esearchresult", {}).get("idlist", []) or []
        if not ids:
            return []

        root = self._request_xml(
            "efetch.fcgi",
            {
                "db": "nlmcatalog",
                "retmode": "xml",
                "id": ",".join(ids),
            },
        )

        journals = []
        for record in root.findall(".//NLMCatalogRecord"):
            parsed = self._parse_nlm_catalog_record(record)
            if parsed:
                journals.append(parsed)

        return journals

    @staticmethod
    def month_to_bounds(from_month, to_month):
        start = datetime.date(from_month.year, from_month.month, 1)
        end_day = calendar.monthrange(to_month.year, to_month.month)[1]
        end = datetime.date(to_month.year, to_month.month, end_day)
        return start, end

    def search_pmids(self, term, from_month, to_month, retmax=1000):
        start, end = self.month_to_bounds(from_month, to_month)
        data = self._request_json(
            "esearch.fcgi",
            {
                "db": "pubmed",
                "retmode": "json",
                "retmax": retmax,
                "term": term,
                "datetype": "pdat",
                "mindate": start.strftime("%Y/%m/%d"),
                "maxdate": end.strftime("%Y/%m/%d"),
                "sort": "pub date",
            },
        )
        return data.get("esearchresult", {}).get("idlist", []) or []

    def fetch_articles(self, pmids):
        if not pmids:
            return []

        root = self._request_xml(
            "efetch.fcgi",
            {
                "db": "pubmed",
                "retmode": "xml",
                "id": ",".join(pmids),
            },
        )
        return [self._parse_article(node) for node in root.findall(".//PubmedArticle")]

    def _parse_article(self, node):
        medline = node.find("MedlineCitation")
        article = medline.find("Article") if medline is not None else None
        pmid = (medline.findtext("PMID") or "").strip() if medline is not None else ""

        title = ""
        if article is not None:
            title = "".join(article.findtext("ArticleTitle") or "").strip()

        abstract = ""
        if article is not None:
            parts = []
            for abstract_text in article.findall("Abstract/AbstractText"):
                label = (abstract_text.attrib.get("Label") or "").strip()
                text = "".join(abstract_text.itertext()).strip()
                if not text:
                    continue
                parts.append(f"{label}: {text}" if label else text)
            abstract = "\n\n".join(parts)

        journal_name = ""
        if article is not None:
            journal_name = (article.findtext("Journal/Title") or "").strip()

        publication_date = self._parse_publication_date(article)
        publication_month = publication_date.replace(day=1) if publication_date else None

        doi = ""
        doi_node = node.find('.//PubmedData/ArticleIdList/ArticleId[@IdType="doi"]')
        if doi_node is not None and doi_node.text:
            doi = doi_node.text.strip().lower()

        pubmed_url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else ""
        article_url = f"https://doi.org/{doi}" if doi else pubmed_url

        mesh_terms = []
        for heading in node.findall(".//MeshHeadingList/MeshHeading"):
            name = (heading.findtext("DescriptorName") or "").strip()
            if name:
                mesh_terms.append(name)

        keywords = []
        for keyword in node.findall(".//KeywordList/Keyword"):
            text = (keyword.text or "").strip()
            if text:
                keywords.append(text)

        publication_types = []
        for publication_type in node.findall(".//PublicationTypeList/PublicationType"):
            text = (publication_type.text or "").strip()
            if text:
                publication_types.append(text)

        return {
            "pmid": pmid,
            "doi": doi,
            "title": title,
            "abstract": abstract,
            "source_journal_name": journal_name,
            "publication_date": publication_date,
            "publication_month": publication_month,
            "article_url": article_url,
            "pubmed_url": pubmed_url,
            "metadata_json": {
                "mesh_terms": sorted(set(mesh_terms)),
                "keywords": sorted(set(keywords)),
                "publication_types": sorted(set(publication_types)),
            },
        }

    def _parse_nlm_catalog_record(self, record):
        title = (record.findtext(".//TitleMain/Title") or "").strip()
        medline_ta = (record.findtext(".//MedlineTA") or "").strip()
        nlm_id = (record.findtext("NlmUniqueID") or "").strip()

        if not title and not medline_ta:
            return None

        issn_print = ""
        issn_electronic = ""
        all_issns = []

        for issn_node in record.findall(".//ISSN"):
            issn = (issn_node.text or "").strip()
            if not issn:
                continue

            all_issns.append(issn)
            issn_type = (issn_node.attrib.get("IssnType") or "").strip().lower()

            if issn_type == "print" and not issn_print:
                issn_print = issn
            elif issn_type == "electronic" and not issn_electronic:
                issn_electronic = issn

        if not issn_print and all_issns:
            issn_print = all_issns[0]
        if not issn_electronic and len(all_issns) > 1:
            for candidate in all_issns:
                if candidate != issn_print:
                    issn_electronic = candidate
                    break

        return {
            "nlm_id": nlm_id,
            "name": title or medline_ta,
            "medline_ta": medline_ta,
            "issn_print": issn_print,
            "issn_electronic": issn_electronic,
        }

    def _parse_publication_date(self, article_node):
        if article_node is None:
            return None

        article_dates = article_node.findall("ArticleDate")
        if article_dates:
            preferred = None
            for node in article_dates:
                if (node.attrib.get("DateType") or "").strip().lower() == "electronic":
                    preferred = node
                    break
            candidate = preferred or article_dates[0]
            parsed = self._parse_structured_date_node(candidate)
            if parsed:
                return parsed

        pub_date = article_node.find("Journal/JournalIssue/PubDate")
        if pub_date is not None:
            parsed_pub_date = self._parse_structured_date_node(pub_date)
            if parsed_pub_date:
                return parsed_pub_date

            medline_date = (pub_date.findtext("MedlineDate") or "").strip()
            if medline_date:
                token = medline_date.split(" ", 1)[0].split("-", 1)[0]
                year = self._to_int(token)
                if year:
                    return datetime.date(year, 1, 1)

        return None

    def _parse_structured_date_node(self, node):
        if node is None:
            return None

        year = self._to_int(node.findtext("Year"))
        month = self._parse_month(node.findtext("Month"))
        day = self._to_int(node.findtext("Day")) or 1

        if not year:
            return None

        month = month or 1
        day = max(1, min(day, calendar.monthrange(year, month)[1]))
        return datetime.date(year, month, day)

    @staticmethod
    def _to_int(value):
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_month(value):
        if not value:
            return None

        text = str(value).strip()
        if text.isdigit():
            month = int(text)
            return month if 1 <= month <= 12 else None

        lower = text.lower()[:3]
        month_map = {
            "jan": 1,
            "feb": 2,
            "mar": 3,
            "apr": 4,
            "may": 5,
            "jun": 6,
            "jul": 7,
            "aug": 8,
            "sep": 9,
            "oct": 10,
            "nov": 11,
            "dec": 12,
        }
        return month_map.get(lower)
