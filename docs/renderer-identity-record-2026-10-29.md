# Rendering-tier identity and yield — 2026-10-29

This is the public source record for the measurements in
[D-renderer-identity](decisions/D-renderer-identity.md). It records what the paid rendering tier
(Firecrawl, `proxy: "basic"`, the shipped `_SCRAPE_OPTIONS`) tells a site about the client, and how
many cited pages each candidate client could read. It is an observation record. The decision is
the decision file; this page is the evidence it rests on.

Every URL below is public. Anyone with a Firecrawl key can repeat the header measurement in one
request.

## Measurement identity

- **Date:** 2026-10-29 (UTC).
- **Corpus:** 79 URLs. 38 were cited in production reports (taken from public
  `/runs/<run-id>/audit.json` records). 41 are journal, news and government pages chosen because
  they are hard to read: publisher paywalls, Cloudflare and Akamai bot walls, DOI redirects.
- **Clients:**
  1. **direct fetch:** `fetch.SourceFetcher` as shipped, `User-Agent: reasonable-answer/1.0
     (citation verification)`.
  2. **Chromium, default UA:** self-hosted headless Chromium (Playwright), whose user agent carries
     the `HeadlessChrome` token.
  3. **Chromium, project UA:** the same browser with its user agent set to `fetch.USER_AGENT`.
  4. **Firecrawl basic:** the provider's v2 scrape with the shipped options.
- **Classifier:** the main text of each response is extracted with trafilatura
  (`favor_recall=True`). Each response is then labelled with the first rule that matches:

  | label | rule |
  |---|---|
  | `error` | the client returned an error and no page |
  | `not_found` | HTTP 404 or 410 |
  | `challenge` | bot-wall wording in the first 20,000 characters, and under 3,000 characters of text |
  | `http_<n>` | any other HTTP status of 400 or more |
  | `empty` | under 300 characters of text |
  | `short` | under 2,500 characters of text |
  | `long` | 2,500 characters of text or more |

  Each cell below gives `label:characters`. "Long body" in the decision means `long`.

## What the provider sends

A Firecrawl `basic` scrape of <https://httpbin.org/headers> returns the request headers the site
received. With the shipped options (no `headers` field), the response was:

```json
{
  "headers": {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/jxl,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.9",
    "Host": "httpbin.org",
    "Priority": "u=0, i",
    "Referer": "https://www.google.com/",
    "Sec-Ch-Ua": "\"Google Chrome\";v=\"155\", \"Chromium\";v=\"155\", \"Not(A:Brand\";v=\"24\"",
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": "\"Linux\"",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/155.0.0.0 Safari/537.36"
  }
}
```

The same scrape with `headers: {"User-Agent": "reasonable-answer/1.0 (citation verification)"}`
(15:14 UTC) changed `User-Agent` and nothing else that identifies the client.
`Referer: https://www.google.com/`, a `Sec-Ch-Ua` naming `"Google Chrome"` and the `Sec-Fetch-*`
headers were still sent. (The Chrome version in `Sec-Ch-Ua` differed between the two scrapes,
156 against 155; the brand did not.)

## Firecrawl with the project user agent

Ten URLs that the default Firecrawl identity read and the direct fetch could not, scraped again
with the `User-Agent` override above:

| URL | result with the project UA |
|---|---|
| <https://doi.org/10.1056/NEJMoa2034577> | provider HTTP 500 |
| <https://www.wsj.com/economy> | provider HTTP 500 |
| <https://www.reuters.com/business/> | provider HTTP 500 |
| <https://doi.org/10.1177/0956797611417632> | provider HTTP 500 |
| <https://www.cbo.gov/publication/44176> | provider HTTP 500 |
| <https://jamanetwork.com/journals/jama/fullarticle/2674671> | provider HTTP 500 |
| <https://www.healthaffairs.org/doi/10.1377/hlthaff.2017.0293> | provider HTTP 500 |
| <https://www.rand.org/pubs/research_reports/RR3106.html> | provider HTTP 500 |
| <https://www.sciencedirect.com/science/article/abs/pii/S0014498307000447> | 403, Elsevier "There was a problem providing the content you requested" page |
| <https://www.bls.gov/cps/> | 200, 28,551 characters |

A control run minutes later with the default identity read WSJ and CBO again, both with HTTP 200.
So the failures follow the identity, not a provider outage.

## Totals

| client | `long` of 79 |
|---|---:|
| direct fetch | 21 |
| Chromium, default UA | 27 |
| Chromium, project UA | 39 |
| Firecrawl basic | 66 |
| direct fetch or Firecrawl (the production ladder) | 68 |
| direct fetch or Chromium with the project UA | 40 |
| direct fetch or Firecrawl or Chromium with the project UA | 72 |

The production ladder reads 28 more bodies than the best honest ladder (68 against 40). A
self-hosted honest browser added to the production ladder reads 4 more (72 against 68).

## Per-URL results

| URL | direct fetch | Chromium, default UA | Chromium, project UA | Firecrawl basic |
|---|---|---|---|---|
| <https://apnews.com/hub/science> | `error:0` | `challenge:243` | `long:7877` | `long:129731` |
| <https://arxiv.org/abs/1706.03762> | `long:3096` | `long:3096` | `long:3096` | `long:8532` |
| <https://ballotpedia.org/Forward_Party> | `empty:157` | `http_403:511` | `challenge:469` | `long:20246` |
| <https://doi.org/10.1001/jama.2020.1585> | `error:0` | `challenge:253` | `long:22004` | `long:93514` |
| <https://doi.org/10.1007/978-3-319-24574-4_28> | `short:314` | `long:4360` | `long:4360` | `long:16032` |
| <https://doi.org/10.1016/S0140-6736(20)30183-5> | `empty:0` | `challenge:257` | `long:8405` | `short:951` |
| <https://doi.org/10.1017/S0140525X0999152X> | `long:8093` | `long:62649` | `long:62649` | `long:172276` |
| <https://doi.org/10.1038/nature14539> | `short:314` | `long:20748` | `long:20748` | `long:56690` |
| <https://doi.org/10.1038/s41586-020-2012-7> | `short:314` | `long:46843` | `long:46843` | `long:86710` |
| <https://doi.org/10.1056/NEJMoa2034577> | `error:0` | `challenge:247` | `challenge:247` | `long:66412` |
| <https://doi.org/10.1073/pnas.1218772110> | `error:0` | `challenge:239` | `long:29450` | `long:58127` |
| <https://doi.org/10.1080/00031305.2016.1154108> | `error:0` | `challenge:261` | `long:29982` | `short:425` |
| <https://doi.org/10.1093/qje/qju022> | `error:0` | `challenge:255` | `short:1686` | `short:453` |
| <https://doi.org/10.1109/CVPR.2016.90> | `empty:157` | `challenge:354` | `short:946` | `empty:0` |
| <https://doi.org/10.1111/j.1540-6261.1992.tb04398.x> | `error:0` | `challenge:269` | `long:92118` | `long:151212` |
| <https://doi.org/10.1126/science.aac4716> | `error:0` | `challenge:253` | `long:31037` | `long:57675` |
| <https://doi.org/10.1136/bmj.n71> | `error:0` | `challenge:460` | `long:30724` | `long:142737` |
| <https://doi.org/10.1145/3442188.3445922> | `error:0` | `challenge:243` | `short:1156` | `long:89041` |
| <https://doi.org/10.1177/0956797611417632> | `error:0` | `challenge:263` | `challenge:263` | `long:60657` |
| <https://doi.org/10.1257/aer.20150572> | `error:0` | `challenge:253` | `short:1707` | `long:4984` |
| <https://doi.org/10.1371/journal.pmed.0020124> | `long:34196` | `long:34196` | `long:34196` | `long:57935` |
| <https://doi.org/10.3389/fpsyg.2013.00863> | `error:0` | `long:68659` | `long:68659` | `long:92446` |
| <https://doi.org/10.3390/ijerph17051729> | `empty:0` | `challenge:209` | `challenge:211` | `long:105113` |
| <https://en.wikipedia.org/wiki/Acland_Mill> | `long:5101` | `long:5161` | `long:5161` | `long:15974` |
| <https://en.wikipedia.org/wiki/Bombay_Spinning_and_Weaving_Company> | `challenge:1082` | `challenge:1082` | `challenge:1082` | `long:7257` |
| <https://en.wikipedia.org/wiki/De-industrialisation_of_India> | `long:30526` | `long:30634` | `long:30634` | `empty:0` |
| <https://en.wikipedia.org/wiki/Economy_of_India_under_the_British_Raj> | `long:42419` | `long:42575` | `long:42575` | `long:214295` |
| <https://en.wikipedia.org/wiki/Tata_Steel> | `long:32982` | `long:34892` | `long:34892` | `long:141355` |
| <https://jamanetwork.com/journals/jama/fullarticle/2674671> | `error:0` | `challenge:253` | `challenge:253` | `long:18173` |
| <https://journals.plos.org/plosmedicine/article?id=10.1371/journal.pmed.1003013> | `long:54564` | `long:54564` | `long:54564` | `long:83052` |
| <https://ourworldindata.org/life-expectancy> | `long:7146` | `long:7146` | `long:7146` | `long:45248` |
| <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2551556> | `error:0` | `http_403:391` | `challenge:253` | `long:9116` |
| <https://pmc.ncbi.nlm.nih.gov/articles/PMC1182327/> | `long:36159` | `long:36159` | `long:36159` | `long:64455` |
| <https://pubmed.ncbi.nlm.nih.gov/16060722/> | `long:4655` | `http_403:13` | `long:7049` | `empty:0` |
| <https://www.aha.org/fact-sheets/2020-01-06-fact-sheet-uncompensated-hospital-care-cost> | `long:7111` | `long:7111` | `long:7111` | `long:9059` |
| <https://www.bls.gov/cps/> | `long:17221` | `challenge:536` | `challenge:536` | `long:37750` |
| <https://www.britannica.com/event/Industrial-Revolution> | `error:0` | `challenge:259` | `long:8510` | `long:33022` |
| <https://www.brookings.edu/articles/the-hutchins-center-explains-the-yield-curve-what-it-is> | `long:10264` | `long:10264` | `long:10264` | `long:19634` |
| <https://www.cbo.gov/publication/44176> | `error:0` | `challenge:0` | `challenge:0` | `long:8729` |
| <https://www.cbo.gov/publication/56811> | `error:0` | `challenge:0` | `challenge:0` | `long:5086` |
| <https://www.cdc.gov/nchs/fastats/life-expectancy.htm> | `short:402` | `empty:258` | `empty:258` | `long:2559` |
| <https://www.census.gov/library/publications/2024/demo/p60-283.html> | `long:3961` | `long:3969` | `long:3969` | `long:16675` |
| <https://www.cms.gov/data-research/statistics-trends-and-reports/medicare-fee-for-service-p> | `short:818` | `short:778` | `short:778` | `short:820` |
| <https://www.commonwealthfund.org/publications/fund-reports/2014/jun/mirror-mirror-wall-201> | `long:9036` | `challenge:271` | `long:9036` | `long:14759` |
| <https://www.economist.com/graphic-detail> | `error:0` | `challenge:257` | `challenge:0` | `long:4547` |
| <https://www.encyclopedia.com/international/encyclopedias-almanacs-transcripts-and-maps/eco> | `long:8304` | `long:8419` | `long:8419` | `long:17607` |
| <https://www.encyclopedia.com/international/encyclopedias-almanacs-transcripts-and-maps/tra> | `long:9735` | `long:9735` | `long:9735` | `long:18451` |
| <https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm> | `short:804` | `short:804` | `short:804` | `long:37633` |
| <https://www.forwardparty.com> | `short:447` | `long:40765` | `long:40765` | `long:70809` |
| <https://www.healthaffairs.org/doi/10.1377/hlthaff.2017.0293> | `error:0` | `challenge:265` | `challenge:265` | `long:96949` |
| <https://www.history.com/articles/industrial-revolution> | `long:10376` | `long:9654` | `long:9654` | `long:26647` |
| <https://www.imf.org/en/Publications/WEO> | `error:0` | `long:31149` | `long:31149` | `long:9817` |
| <https://www.investopedia.com/terms/i/inflation.asp> | `empty:0` | `challenge:110` | `long:24448` | `empty:0` |
| <https://www.iosrjournals.org/iosr-jhss/papers/Vol.28-Issue4/Ser-1/C2804010913.pdf> | `empty:0` | `error:0` | `error:0` | `long:22649` |
| <https://www.journals.uchicago.edu/doi/10.1086/505095> | `error:0` | `challenge:273` | `not_found:369` | `empty:194` |
| <https://www.jstor.org/stable/1914185> | `short:314` | `challenge:272` | `challenge:272` | `long:4987` |
| <https://www.jstor.org/stable/41332564> | `short:314` | `challenge:272` | `challenge:272` | `long:3758` |
| <https://www.lse.ac.uk/Economic-History/Assets/Documents/Research/GEHN/GEHNConferences/conf> | `error:0` | `error:0` | `error:0` | `long:83233` |
| <https://www.nap.edu/catalog/12511/americas-uninsured-crisis-consequences-for-health-and-he> | `empty:217` | `long:6326` | `long:6326` | `long:41656` |
| <https://www.nber.org/papers/w19843> | `short:1708` | `short:1437` | `short:1437` | `long:12429` |
| <https://www.nber.org/system/files/working_papers/w10586/w10586.pdf> | `empty:0` | `error:0` | `error:0` | `long:72795` |
| <https://www.ncbi.nlm.nih.gov/books/NBK209714/> | `challenge:131` | `challenge:131` | `challenge:131` | `empty:0` |
| <https://www.nejm.org/doi/full/10.1056/NEJMp0708558> | `error:0` | `challenge:247` | `challenge:247` | `long:25672` |
| <https://www.nytimes.com/interactive/2021/us/covid-cases.html> | `error:0` | `challenge:0` | `challenge:0` | `empty:0` |
| <https://www.oecd-ilibrary.org/social-issues-migration-health/health-at-a-glance-2023_7a7af> | `error:0` | `challenge:265` | `error:0` | `long:895461` |
| <https://www.peri.umass.edu/publication/item/1127-economic-analysis-of-medicare-for-all> | `short:1377` | `short:1377` | `short:1377` | `short:2097` |
| <https://www.pewresearch.org/politics/2024/06/24/public-trust-in-government-1958-2024/> | `long:15518` | `long:29319` | `long:29319` | `long:33393` |
| <https://www.rand.org/pubs/research_reports/RR3106.html> | `error:0` | `http_403:511` | `http_403:511` | `long:53413` |
| <https://www.researchgate.net/publication/7614734_Why_Most_Published_Research_Findings_Are_> | `error:0` | `challenge:211` | `challenge:146` | `long:74901` |
| <https://www.reuters.com/business/> | `error:0` | `challenge:0` | `challenge:0` | `long:6759` |
| <https://www.savehandloom.org/how-british-rule-broke-indias-textile-industry-and-left-our-w> | `empty:0` | `long:4783` | `long:4783` | `long:5862` |
| <https://www.sciencedirect.com/science/article/abs/pii/S0014498307000447> | `error:0` | `http_403:377` | `http_403:310` | `long:28384` |
| <https://www.semanticscholar.org/paper/Attention-is-All-you-Need-Vaswani-Shazeer/204e307387> | `empty:157` | `challenge:171` | `challenge:171` | `long:28366` |
| <https://www.statista.com/statistics/263264/top-companies-in-the-world-by-market-capitaliza> | `short:2025` | `long:5067` | `long:5067` | `empty:0` |
| <https://www.theguardian.com/science> | `challenge:0` | `challenge:741` | `challenge:741` | `long:23384` |
| <https://www.washingtonpost.com/politics/> | `empty:0` | `error:0` | `error:0` | `long:20965` |
| <https://www.who.int/news-room/fact-sheets/detail/obesity-and-overweight> | `long:14017` | `long:14017` | `long:14017` | `long:16760` |
| <https://www.worldbank.org/en/topic/poverty/overview> | `short:2041` | `short:1147` | `short:1147` | `long:14499` |
| <https://www.wsj.com/economy> | `error:0` | `challenge:0` | `challenge:0` | `long:19268` |
