# dynamic_v2 packet inspection - offset 120, batch 4, budget 1800 tokens

index: {'indexed_segments': 239, 'term_keys': 15, 'entity_keys': 30, 'keyword_keys': 957, 'section_keys': 0}
section: None

## current batch
1. Germany has also invested in research on uncrewed aircraft systems, or UAS.
2. Uncrewed aviation covers a broad range of aircraft and missions, from small drones used for inspection and photography to sophisticated systems designed for autonomous transportation.
3. The development of these systems has expanded the potential applications of aviation beyond traditional passenger and cargo services.
4. One important application is infrastructure inspection.

## query profile
terms: ['Germany', 'uncrewed aircraft systems, or UAS', 'Uncrewed aviation']
entities: ['Germany']
keywords: ['germany', 'invested', 'research', 'uncrewed', 'aircraft', 'systems', 'uas', 'aviation', 'covers', 'broad', 'range', 'missions', 'small', 'drones', 'used', 'inspection', 'photography', 'sophisticated', 'designed', 'autonomous', 'transportation', 'development', 'expanded', 'potential', 'applications', 'beyond', 'traditional', 'passenger', 'cargo', 'services', 'important', 'application', 'infrastructure']

## candidate pool by source
{"distant_segment": 8, "rule": 1, "glossary": 15, "entity": 5, "local_context": 5, "summary": 3}

## selected context (rank order, score features)
- rule:style | rule | score=60.0 | tokens=14 | authority=project_confirmed_knowledge | features={'explicit_rule': 60.0}
- glossary:t-a8a95109fef7 | glossary | score=42.4242 | tokens=13 | authority=automatic_observation | features={'lexical_overlap': 2.4242, 'shared_term': 40.0}
- glossary:t-7c8a3fe5a47a | glossary | score=41.2121 | tokens=7 | authority=automatic_observation | features={'lexical_overlap': 1.2121, 'shared_term': 40.0}
- glossary:t-53fe57a066d0 | glossary | score=40.6061 | tokens=4 | authority=automatic_observation | features={'lexical_overlap': 0.6061, 'shared_term': 40.0}
- glossary:t-c5ae7686eddc | glossary | score=40.0 | tokens=4 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-ed042984ee10 | glossary | score=40.0 | tokens=4 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-43efa5a7f62d | glossary | score=40.0 | tokens=5 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-4de404c77021 | glossary | score=40.0 | tokens=5 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-cbe41653671b | glossary | score=40.0 | tokens=5 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-87ddcb5b3a3b | glossary | score=40.0 | tokens=7 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-92d4f3b7db80 | glossary | score=40.0 | tokens=9 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-ea4853018ab6 | glossary | score=40.0 | tokens=9 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-f40fd03d3ac3 | glossary | score=40.0 | tokens=11 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-f5b520f9594b | glossary | score=40.0 | tokens=11 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-068af3acf604 | glossary | score=40.0 | tokens=15 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-7896bada5f8b | glossary | score=40.0 | tokens=27 | authority=automatic_observation | features={'shared_term': 40.0}
- entity:entity-0001 | entity | score=30.0 | tokens=7 | authority=automatic_observation | features={'shared_entity': 30.0}
- entity:entity-0005 | entity | score=30.0 | tokens=9 | authority=automatic_observation | features={'shared_entity': 30.0}
- entity:entity-0012 | entity | score=30.0 | tokens=9 | authority=automatic_observation | features={'shared_entity': 30.0}
- entity:entity-0003 | entity | score=30.0 | tokens=10 | authority=automatic_observation | features={'shared_entity': 30.0}
- entity:entity-0002 | entity | score=30.0 | tokens=15 | authority=automatic_observation | features={'shared_entity': 30.0}
- distant:64 | distant_segment | score=65.4242 | tokens=61 | authority=project_confirmed_knowledge | features={'distance_penalty': -7.0, 'lexical_overlap': 2.4242, 'shared_confirmed_term': 40.0, 'shared_entity': 30.0}
- distant:60 | distant_segment | score=64.3182 | tokens=25 | authority=project_confirmed_knowledge | features={'distance_penalty': -7.5, 'lexical_overlap': 1.8182, 'shared_confirmed_term': 40.0, 'shared_entity': 30.0}
- distant:58 | distant_segment | score=64.0682 | tokens=82 | authority=project_confirmed_knowledge | features={'distance_penalty': -7.75, 'lexical_overlap': 1.8182, 'shared_confirmed_term': 40.0, 'shared_entity': 30.0}
- distant:40 | distant_segment | score=61.2121 | tokens=40 | authority=project_confirmed_knowledge | features={'distance_penalty': -10.0, 'lexical_overlap': 1.2121, 'shared_confirmed_term': 40.0, 'shared_entity': 30.0}
- distant:39 | distant_segment | score=61.0871 | tokens=51 | authority=project_confirmed_knowledge | features={'distance_penalty': -10.125, 'lexical_overlap': 1.2121, 'shared_confirmed_term': 40.0, 'shared_entity': 30.0}
- distant:36 | distant_segment | score=60.7121 | tokens=42 | authority=project_confirmed_knowledge | features={'distance_penalty': -10.5, 'lexical_overlap': 1.2121, 'shared_confirmed_term': 40.0, 'shared_entity': 30.0}
- distant:20 | distant_segment | score=58.7121 | tokens=43 | authority=project_confirmed_knowledge | features={'distance_penalty': -12.5, 'lexical_overlap': 1.2121, 'shared_confirmed_term': 40.0, 'shared_entity': 30.0}
- distant:8 | distant_segment | score=57.2121 | tokens=65 | authority=project_confirmed_knowledge | features={'distance_penalty': -14.0, 'lexical_overlap': 1.2121, 'shared_confirmed_term': 40.0, 'shared_entity': 30.0}
- local:prev:1 | local_context | score=27.4242 | tokens=52 | authority=raw_distant_context | features={'direct_neighbour': 25.0, 'lexical_overlap': 2.4242}
- local:target:0 | local_context | score=27.4242 | tokens=64 | authority=automatic_observation | features={'direct_neighbour': 25.0, 'lexical_overlap': 2.4242}
- local:next:1 | local_context | score=26.8182 | tokens=35 | authority=raw_distant_context | features={'direct_neighbour': 25.0, 'lexical_overlap': 1.8182}
- summary:section:unit-0002 | summary | score=26.8182 | tokens=205 | authority=raw_distant_context | features={'document_context': 15.0, 'lexical_overlap': 1.8182, 'same_section': 10.0}
- local:next:0 | local_context | score=26.2121 | tokens=34 | authority=raw_distant_context | features={'direct_neighbour': 25.0, 'lexical_overlap': 1.2121}
- local:prev:0 | local_context | score=25.0 | tokens=29 | authority=raw_distant_context | features={'direct_neighbour': 25.0}
- summary:profile | summary | score=15.6061 | tokens=45 | authority=raw_distant_context | features={'document_context': 15.0, 'lexical_overlap': 0.6061}
- summary:document | summary | score=15.6061 | tokens=137 | authority=raw_distant_context | features={'document_context': 15.0, 'lexical_overlap': 0.6061}

## selected tokens by source
{"rule": 14, "glossary": 136, "entity": 50, "distant_segment": 409, "local_context": 214, "summary": 387}
total selected tokens: 1210
rejected candidates: 0
consolidated duplicates: 7
priority (batch-matched constraints): 20
distant retrieved/selected: 8/8

## distant hits
- segment 64 distance 56 retrieval=both terms=['Germany'] entities=['Germany'] keywords=['germany', 'research', 'aviation', 'important']
- segment 60 distance 60 retrieval=both terms=['Germany', 'Uncrewed aviation'] entities=['Germany'] keywords=['germany', 'uncrewed', 'aviation']
- segment 58 distance 62 retrieval=both terms=['Germany'] entities=['Germany'] keywords=['germany', 'research', 'development']
- segment 40 distance 80 retrieval=both terms=['Germany'] entities=['Germany'] keywords=['development', 'important']
- segment 39 distance 81 retrieval=both terms=['Germany'] entities=['Germany'] keywords=['germany', 'research']
- segment 36 distance 84 retrieval=both terms=['Germany'] entities=['Germany'] keywords=['research', 'important']
- segment 20 distance 100 retrieval=both terms=['Germany'] entities=['Germany'] keywords=['development', 'services']
- segment 8 distance 112 retrieval=both terms=['Germany'] entities=['Germany'] keywords=['germany', 'important']

## rendered packet
【文档画像】
领域：经济学/航天经济
细分领域：航天经济与城市空中交通
文类：国际组织行业报告/政策报告
读者：政策制定者、行业分析师、研究人员及航天产业从业者
语域：正式书面语
风格约束：保持正式、客观、中性的报告语体；统一航天、无人机、UAM/UAS等术语与缩写；保留ESA、EUMETSAT、Artemis等机构与项目名称；避免口语化表达；长句需逻辑清晰、信息完整。

【文体与翻译规则】
表达规范、中性、清晰，避免口语化；术语优先遵循术语库，专有名词采用通行译法；保留数字、日期、引用标注与 URL。

【锁定术语与范围规则（项目人工锁定优先）】
【建议术语（仅供参考，请优先采用）】：
- uncrewed aircraft systems, or UAS -> 无人航空器系统（UAS）（ID: t-a8a95109fef7）
- Uncrewed aviation -> 无人航空（ID: t-7c8a3fe5a47a）
- Germany -> 德国（ID: t-53fe57a066d0）
- Cologne -> 科隆（ID: t-c5ae7686eddc）
- New Space -> 新太空（ID: t-ed042984ee10）
- Artemis -> 阿尔忒弥斯计划（ID: t-43efa5a7f62d）
- Darmstadt -> 达姆施塔特（ID: t-4de404c77021）
- UAM -> 城市空中交通（UAM）（ID: t-cbe41653671b）
- Urban Air Mobility -> 城市空中交通（ID: t-87ddcb5b3a3b）
- European Astronaut Centre -> 欧洲宇航员中心（ID: t-92d4f3b7db80）
- European lunar initiatives -> 欧洲月球倡议（ID: t-ea4853018ab6）
- European Space Agency (ESA) -> 欧洲空间局（ESA）（ID: t-f40fd03d3ac3）
- European Space Operations Centre -> 欧洲空间操作中心（ID: t-f5b520f9594b）
- OECD The Space Economy at a Glance 2026 -> OECD《2026年太空经济概览》（ID: t-068af3acf604）
- European Organisation for the Exploitation of Meteorological Satellites (EUMETSAT) -> 欧洲气象卫星开发组织（EUMETSAT）（ID: t-7896bada5f8b）

【当前语义单元摘要】
本单元主要讨论 eVTOL/UAM 的电池容量与重量权衡、推进架构、安全、自动化、空域整合、垂直起降设施、与现有交通系统的关系、货运与无人航空器系统应用，并涉及德国在 UAS 研究、公众接受、噪声、环境、经济性、监管与合作方面的路径；随后进入第三部分，转向德国通过 ESA、DLR 及航天企业参与 NASA Artemis 计划、月球探索与 Orion 欧洲服务舱等未来太空发展。

【当前单元翻译提示】
整体为技术说明与政策、产业评述语气，应使用客观、正式、学术化的简体中文，避免宣传性措辞。、情态词 may、could、likely、would 等译为“可能/或将/会”等，保留不确定性，不可译成确定事实。、术语首现可括注英文缩写：UAM（城市空中交通）、eVTOL（电动垂直起降飞行器）、UAS（无人航空器系统）、ESA（欧洲空间局）、DLR（德国航空航天中心）、ESM（欧洲服务舱）。、Vertidromes 非常见拼写，若上下文对应垂直起降场，宜统一为“垂直起降场”或保留 vertidrome 并首次括注，避免与 vertiport 混用。、distributed electric propulsion 统一译为“分布式电推进”；thermal management 为“热管理”；fault detection 为“故障检测”；air traffic management 为“空中交通管理”。、Part III 标题译为“第三部分 德国在月球探索与未来太空发展中的角色”，保持标题编号与层级。、NASA、ESA、DLR、Artemis、Orion、European Service Module 等专名按中文通行译法：美国国家航空航天局、欧洲空间局、德国航空航天中心、阿尔忒弥斯计划、猎户座飞船、欧洲服务舱。、保持 UAM 与 uncrewed aviation 的区分：前者指城市空中交通系统，后者指无人航空/无人机航空活动，不可混译。

【全文概要】
材料围绕德国与欧洲的航天/太空发展以及城市空中交通与无人航空展开。第一部分概述德国在欧洲航天经济中的地位、商业航天与 New Space 趋势、与 ESA、欧盟空间计划、EUMETSAT、DLR 等机构和项目的合作，并涉及卫星服务、地球观测、卫星导航、商业发射、人工智能、星上智能处理、政府政策、监管与可持续性挑战；随后转向城市空中交通（UAM）与无人航空，介绍 eVTOL 技术基础、垂直起降设施、系统级研究和飞机设计挑战。第二部分继续讨论 eVTOL/UAM 的电池容量与重量权衡、推进架构、安全、自动化、空域整合、垂直起降设施、与现有交通系统的关系、货运与无人航空器系统应用，并涉及德国在 UAS 研究、公众接受、噪声、环境、经济性、监管与合作方面的路径；之后进入第三部分，转向德国通过 ESA、DLR 及航天企业参与 NASA 阿尔忒弥斯计划、月球探索与猎户座飞船欧洲服务舱等未来太空发展。第三部分介绍欧洲服务舱（ESM）及其对推进、发电、热控、水供应、生命保障等关键功能的支持，说明德国在相关部件研制中的作用；并系统阐述未来月球探索所需的技术与模式，包括自主机器人、通信导航、能源、原位资源利用、商业参与和可持续治理，强调国际合作、德国技术能力以及从短期任务向长期月球开发的转变。

【专名/实体连续性提示（仅作建议；人工实体选择优先）】
- OECD -> OECD（organization）
- ESA -> 欧洲空间局（ESA）（organization）
- EUMETSAT -> EUMETSAT（organization）
- Space Economy -> 太空经济（named_concept）
- The Space Economy at a Glance 2026 -> 《2026年太空经济概览》（book）

【前文原文上下文】
- Compared with passenger transportation, cargo operations may encounter fewer challenges related to public acceptance and passenger safety, potentially allowing some commercial applications to develop earlier.
- Medical logistics, laboratory samples, urgent spare parts and other time-sensitive products are possible use cases.

【前文已接受译文连续性】
- 段 119 [reviewed] 原文：Compared with passenger transportation, cargo operations may encounter fewer challenges related to public acceptance and passenger safety, potentially allowing some commercial applications to develop earlier.
  译文：[accepted target 119]

【后文原文上下文】
- Compared with conventional inspection methods, uncrewed systems can reduce the need for workers to access difficult or dangerous locations.
- Drones can collect high-resolution images and sensor data from bridges, railways, power lines, wind turbines and other infrastructure.

【远距离相关段落（历史上下文，仅供一致性参考）】
- [段 65 · 距离 56] 原文：Germany has become an important European centre for research into UAM because of its established aerospace industry, advanced aviation research institutions and extensive engineering capabilities.
  已接受译文：[accepted target 64]
- [段 61 · 距离 60] 原文：Urban Air Mobility and Uncrewed Aviation in Germany
  已接受译文：[accepted target 60]
- [段 59 · 距离 62] 原文：By combining industrial capabilities, scientific research, public investment and commercial innovation, Germany aims to strengthen its role in Europe’s space activities and contribute to the development of a more competitive, sustainable and interconnected global space economy.
  已接受译文：[accepted target 58]
- [段 41 · 距离 80] 原文：Technological innovation is becoming increasingly important in the future development of Germany’s space economy.
  已接受译文：[accepted target 40]
- [段 40 · 距离 81] 原文：Through collaboration among government agencies, research institutions and businesses, Germany has established a highly integrated innovation environment.
  已接受译文：[accepted target 39]
- [段 37 · 距离 84] 原文：The relationship between research and industry represents another important characteristic of Germany’s space ecosystem.
  已接受译文：[accepted target 36]
- [段 21 · 距离 100] 原文：A significant feature of Germany’s commercial space development is the increasing importance of satellite-based services.
  已接受译文：[accepted target 20]
- [段 9 · 距离 112] 原文：As one of the founding members of ESA, Germany has consistently played an important role in shaping European space activities through financial contributions, technological expertise and industrial participation.
  已接受译文：[accepted target 8]

【待翻译段落（按序号返回等长数组）】
1. Germany has also invested in research on uncrewed aircraft systems, or UAS.
2. Uncrewed aviation covers a broad range of aircraft and missions, from small drones used for inspection and photography to sophisticated systems designed for autonomous transportation.
3. The development of these systems has expanded the potential applications of aviation beyond traditional passenger and cargo services.
4. One important application is infrastructure inspection.

## render category chars
{"document_context": 1575, "style": 66, "glossary": 907, "entities": 226, "neighbor_context": 896, "distant_context": 1669, "source": 480}
truncated: False