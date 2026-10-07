# dynamic_v2 packet inspection - offset 40, batch 4, budget 1800 tokens

index: {'indexed_segments': 239, 'term_keys': 15, 'entity_keys': 30, 'keyword_keys': 957, 'section_keys': 0}
section: None

## current batch
1. Technological innovation is becoming increasingly important in the future development of Germany’s space economy.
2. Artificial intelligence, advanced data processing, automation and digital technologies are changing the way satellite systems operate.
3. Traditional satellite missions mainly collected data and transmitted it back to Earth for analysis.
4. However, new-generation systems increasingly incorporate intelligent processing capabilities directly onboard satellites.

## query profile
terms: ['Germany']
entities: ['Space Economy', 'Germany', 'Artificial intelligence', 'Earth']
keywords: ['technological', 'innovation', 'becoming', 'increasingly', 'important', 'future', 'development', 'germany’s', 'space', 'economy', 'artificial', 'intelligence', 'advanced', 'data', 'processing', 'automation', 'digital', 'technologies', 'changing', 'way', 'satellite', 'systems', 'operate', 'traditional', 'missions', 'mainly', 'collected', 'transmitted', 'back', 'earth', 'analysis', 'however', 'new-generation', 'incorporate', 'intelligent', 'capabilities', 'directly', 'onboard', 'satellites']

## candidate pool by source
{"distant_segment": 8, "rule": 1, "glossary": 15, "entity": 5, "summary": 3, "local_context": 5}

## selected context (rank order, score features)
- rule:style | rule | score=60.0 | tokens=14 | authority=project_confirmed_knowledge | features={'explicit_rule': 60.0}
- glossary:t-068af3acf604 | glossary | score=41.0256 | tokens=15 | authority=automatic_observation | features={'lexical_overlap': 1.0256, 'shared_term': 40.0}
- glossary:t-ed042984ee10 | glossary | score=40.5128 | tokens=4 | authority=automatic_observation | features={'lexical_overlap': 0.5128, 'shared_term': 40.0}
- glossary:t-f40fd03d3ac3 | glossary | score=40.5128 | tokens=11 | authority=automatic_observation | features={'lexical_overlap': 0.5128, 'shared_term': 40.0}
- glossary:t-f5b520f9594b | glossary | score=40.5128 | tokens=11 | authority=automatic_observation | features={'lexical_overlap': 0.5128, 'shared_term': 40.0}
- glossary:t-a8a95109fef7 | glossary | score=40.5128 | tokens=13 | authority=automatic_observation | features={'lexical_overlap': 0.5128, 'shared_term': 40.0}
- glossary:t-7896bada5f8b | glossary | score=40.5128 | tokens=27 | authority=automatic_observation | features={'lexical_overlap': 0.5128, 'shared_term': 40.0}
- glossary:t-53fe57a066d0 | glossary | score=40.0 | tokens=4 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-c5ae7686eddc | glossary | score=40.0 | tokens=4 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-43efa5a7f62d | glossary | score=40.0 | tokens=5 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-4de404c77021 | glossary | score=40.0 | tokens=5 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-cbe41653671b | glossary | score=40.0 | tokens=5 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-7c8a3fe5a47a | glossary | score=40.0 | tokens=7 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-87ddcb5b3a3b | glossary | score=40.0 | tokens=7 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-92d4f3b7db80 | glossary | score=40.0 | tokens=9 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-ea4853018ab6 | glossary | score=40.0 | tokens=9 | authority=automatic_observation | features={'shared_term': 40.0}
- entity:entity-0003 | entity | score=31.0256 | tokens=10 | authority=automatic_observation | features={'lexical_overlap': 1.0256, 'shared_entity': 30.0}
- entity:entity-0002 | entity | score=31.0256 | tokens=15 | authority=automatic_observation | features={'lexical_overlap': 1.0256, 'shared_entity': 30.0}
- entity:entity-0001 | entity | score=30.0 | tokens=7 | authority=automatic_observation | features={'shared_entity': 30.0}
- entity:entity-0005 | entity | score=30.0 | tokens=9 | authority=automatic_observation | features={'shared_entity': 30.0}
- entity:entity-0012 | entity | score=30.0 | tokens=9 | authority=automatic_observation | features={'shared_entity': 30.0}
- distant:36 | distant_segment | score=71.0385 | tokens=42 | authority=project_confirmed_knowledge | features={'distance_penalty': -0.5, 'lexical_overlap': 1.5385, 'shared_confirmed_term': 40.0, 'shared_entity': 30.0}
- distant:28 | distant_segment | score=70.5513 | tokens=55 | authority=project_confirmed_knowledge | features={'distance_penalty': -1.5, 'lexical_overlap': 2.0513, 'shared_confirmed_term': 40.0, 'shared_entity': 30.0}
- distant:20 | distant_segment | score=69.0385 | tokens=42 | authority=project_confirmed_knowledge | features={'distance_penalty': -2.5, 'lexical_overlap': 1.5385, 'shared_confirmed_term': 40.0, 'shared_entity': 30.0}
- distant:14 | distant_segment | score=68.2885 | tokens=55 | authority=project_confirmed_knowledge | features={'distance_penalty': -3.25, 'lexical_overlap': 1.5385, 'shared_confirmed_term': 40.0, 'shared_entity': 30.0}
- distant:10 | distant_segment | score=67.7885 | tokens=55 | authority=project_confirmed_knowledge | features={'distance_penalty': -3.75, 'lexical_overlap': 1.5385, 'shared_confirmed_term': 40.0, 'shared_entity': 30.0}
- distant:1 | distant_segment | score=67.6891 | tokens=30 | authority=project_confirmed_knowledge | features={'distance_penalty': -4.875, 'lexical_overlap': 2.5641, 'shared_confirmed_term': 40.0, 'shared_entity': 30.0}
- distant:8 | distant_segment | score=67.5385 | tokens=65 | authority=project_confirmed_knowledge | features={'distance_penalty': -4.0, 'lexical_overlap': 1.5385, 'shared_confirmed_term': 40.0, 'shared_entity': 30.0}
- distant:3 | distant_segment | score=67.4263 | tokens=30 | authority=project_confirmed_knowledge | features={'distance_penalty': -4.625, 'lexical_overlap': 2.0513, 'shared_confirmed_term': 40.0, 'shared_entity': 30.0}
- summary:section:unit-0001 | summary | score=28.0769 | tokens=173 | authority=raw_distant_context | features={'document_context': 15.0, 'lexical_overlap': 3.0769, 'same_section': 10.0}
- local:next:1 | local_context | score=26.5385 | tokens=21 | authority=raw_distant_context | features={'direct_neighbour': 25.0, 'lexical_overlap': 1.5385}
- local:prev:0 | local_context | score=26.0256 | tokens=42 | authority=raw_distant_context | features={'direct_neighbour': 25.0, 'lexical_overlap': 1.0256}
- local:prev:1 | local_context | score=25.5128 | tokens=39 | authority=raw_distant_context | features={'direct_neighbour': 25.0, 'lexical_overlap': 0.5128}
- local:target:0 | local_context | score=25.5128 | tokens=50 | authority=automatic_observation | features={'direct_neighbour': 25.0, 'lexical_overlap': 0.5128}
- local:next:0 | local_context | score=25.0 | tokens=31 | authority=raw_distant_context | features={'direct_neighbour': 25.0}
- summary:document | summary | score=15.5128 | tokens=137 | authority=raw_distant_context | features={'document_context': 15.0, 'lexical_overlap': 0.5128}
- summary:profile | summary | score=15.0 | tokens=45 | authority=raw_distant_context | features={'document_context': 15.0}

## selected tokens by source
{"rule": 14, "glossary": 136, "entity": 50, "distant_segment": 374, "summary": 355, "local_context": 183}
total selected tokens: 1112
rejected candidates: 0
consolidated duplicates: 7
priority (batch-matched constraints): 20
distant retrieved/selected: 8/8

## distant hits
- segment 36 distance 4 retrieval=both terms=['Germany'] entities=['Germany'] keywords=['important', 'germany’s', 'space']
- segment 28 distance 12 retrieval=both terms=['Germany'] entities=['Germany'] keywords=['space', 'advanced', 'changing', 'capabilities']
- segment 20 distance 20 retrieval=both terms=['Germany'] entities=['Germany'] keywords=['development', 'germany’s', 'space']
- segment 14 distance 26 retrieval=both terms=['Germany'] entities=['Space Economy', 'Germany'] keywords=['innovation', 'space', 'economy']
- segment 10 distance 30 retrieval=both terms=['Germany'] entities=['Germany'] keywords=['germany’s', 'space', 'satellite']
- segment 1 distance 39 retrieval=both terms=['Germany'] entities=['Space Economy', 'Germany'] keywords=['future', 'development', 'germany’s', 'space', 'economy']
- segment 8 distance 32 retrieval=both terms=['Germany'] entities=['Germany'] keywords=['technological', 'important', 'space']
- segment 3 distance 37 retrieval=both terms=['Germany'] entities=['Space Economy', 'Germany'] keywords=['development', 'germany’s', 'space', 'economy']

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
- OECD The Space Economy at a Glance 2026 -> OECD《2026年太空经济概览》（ID: t-068af3acf604）
- New Space -> 新太空（ID: t-ed042984ee10）
- European Space Agency (ESA) -> 欧洲空间局（ESA）（ID: t-f40fd03d3ac3）
- European Space Operations Centre -> 欧洲空间操作中心（ID: t-f5b520f9594b）
- uncrewed aircraft systems, or UAS -> 无人航空器系统（UAS）（ID: t-a8a95109fef7）
- European Organisation for the Exploitation of Meteorological Satellites (EUMETSAT) -> 欧洲气象卫星开发组织（EUMETSAT）（ID: t-7896bada5f8b）
- Germany -> 德国（ID: t-53fe57a066d0）
- Cologne -> 科隆（ID: t-c5ae7686eddc）
- Artemis -> 阿尔忒弥斯计划（ID: t-43efa5a7f62d）
- Darmstadt -> 达姆施塔特（ID: t-4de404c77021）
- UAM -> 城市空中交通（UAM）（ID: t-cbe41653671b）
- Uncrewed aviation -> 无人航空（ID: t-7c8a3fe5a47a）
- Urban Air Mobility -> 城市空中交通（ID: t-87ddcb5b3a3b）
- European Astronaut Centre -> 欧洲宇航员中心（ID: t-92d4f3b7db80）
- European lunar initiatives -> 欧洲月球倡议（ID: t-ea4853018ab6）

【当前语义单元摘要】
本单元由两部分组成：第一部分概述德国在欧洲航天/太空经济中的地位、商业航天发展、与ESA等机构合作、New Space趋势、卫星服务、商业发射、政府政策、AI与创新，以及监管和可持续性挑战；第二部分转向城市空中交通（UAM）与无人航空，介绍eVTOL技术基础、垂直起降设施、系统级研究和飞机设计挑战。

【当前单元翻译提示】
文体为报告/政策分析，语气正式、客观，翻译应避免口语化。、space 在德国航天政策语境中可统一为“航天”或“太空”；标题 Germany’s Space Economy 可作“德国太空经济/德国航天经济”，但同一单元内需保持一致。、New Space 可译为“新太空”或保留 New Space，首次出现可加注。、upstream/downstream 建议译为“上游/下游”；launcher 译为“运载器/发射器”，商业发射语境可用“发射”。、UAM 译为“城市空中交通”，uncrewed aviation 译为“无人航空”，eVTOL 保留英文并译“电动垂直起降飞行器”。、vertiports/vertidromes 可统一为“垂直起降场”，若需区分可保留英文原词。、机构缩写 ESA、EUMETSAT、DLR 首次出现给中文全称并保留缩写，后文沿用缩写。、注意指代连续性：These institutions 指前文科隆、达姆施塔特和 EUMETSAT 总部等机构；This development 指德国航天商业化和 New Space 趋势；Such technologies 指 AI 数据处理/星上处理；Such systems 指 UAM 运输系统。

【全文概要】
材料围绕德国与欧洲的航天/太空发展以及城市空中交通与无人航空展开。第一部分概述德国在欧洲航天经济中的地位、商业航天与 New Space 趋势、与 ESA、欧盟空间计划、EUMETSAT、DLR 等机构和项目的合作，并涉及卫星服务、地球观测、卫星导航、商业发射、人工智能、星上智能处理、政府政策、监管与可持续性挑战；随后转向城市空中交通（UAM）与无人航空，介绍 eVTOL 技术基础、垂直起降设施、系统级研究和飞机设计挑战。第二部分继续讨论 eVTOL/UAM 的电池容量与重量权衡、推进架构、安全、自动化、空域整合、垂直起降设施、与现有交通系统的关系、货运与无人航空器系统应用，并涉及德国在 UAS 研究、公众接受、噪声、环境、经济性、监管与合作方面的路径；之后进入第三部分，转向德国通过 ESA、DLR 及航天企业参与 NASA 阿尔忒弥斯计划、月球探索与猎户座飞船欧洲服务舱等未来太空发展。第三部分介绍欧洲服务舱（ESM）及其对推进、发电、热控、水供应、生命保障等关键功能的支持，说明德国在相关部件研制中的作用；并系统阐述未来月球探索所需的技术与模式，包括自主机器人、通信导航、能源、原位资源利用、商业参与和可持续治理，强调国际合作、德国技术能力以及从短期任务向长期月球开发的转变。

【专名/实体连续性提示（仅作建议；人工实体选择优先）】
- Space Economy -> 太空经济（named_concept）
- The Space Economy at a Glance 2026 -> 《2026年太空经济概览》（book）
- OECD -> OECD（organization）
- ESA -> 欧洲空间局（ESA）（organization）
- EUMETSAT -> EUMETSAT（organization）

【前文原文上下文】
- The German Aerospace Center (DLR) plays a central role in this network by conducting research, managing national programmes and supporting technological development.
- Through collaboration among government agencies, research institutions and businesses, Germany has established a highly integrated innovation environment.

【前文已接受译文连续性】
- 段 39 [reviewed] 原文：Through collaboration among government agencies, research institutions and businesses, Germany has established a highly integrated innovation environment.
  译文：[accepted target 39]

【后文原文上下文】
- Artificial intelligence has particular potential in Earth observation applications.
- This enables faster decision-making, reduces communication requirements and improves the efficiency of space-based services.

【远距离相关段落（历史上下文，仅供一致性参考）】
- [段 37 · 距离 4] 原文：The relationship between research and industry represents another important characteristic of Germany’s space ecosystem.
  已接受译文：[accepted target 36]
- [段 29 · 距离 12] 原文：By combining private investment, advanced engineering capabilities and public support mechanisms, Germany seeks to enhance its role within the changing global space market.
  已接受译文：[accepted target 28]
- [段 21 · 距离 20] 原文：A significant feature of Germany’s commercial space development is the increasing importance of satellite-based services.
  已接受译文：[accepted target 20]
- [段 15 · 距离 26] 原文：According to recent assessments of the German space economy, Germany possesses a comprehensive industrial and innovation base covering almost the entire space value chain.
  已接受译文：[accepted target 14]
- [段 11 · 距离 30] 原文：These institutions have contributed significantly to Germany’s role as a centre for space research, astronaut training, satellite operations and meteorological observation.
  已接受译文：[accepted target 10]
- [段 2 · 距离 39] 原文：Germany’s Space Economy, Urban Air Mobility and Future Space Development
  已接受译文：[accepted target 1]
- [段 9 · 距离 32] 原文：As one of the founding members of ESA, Germany has consistently played an important role in shaping European space activities through financial contributions, technological expertise and industrial participation.
  已接受译文：[accepted target 8]
- [段 4 · 距离 37] 原文：Germany’s Commercial Space Sector and the Development of Its Space Economy
  已接受译文：[accepted target 3]

【待翻译段落（按序号返回等长数组）】
1. Technological innovation is becoming increasingly important in the future development of Germany’s space economy.
2. Artificial intelligence, advanced data processing, automation and digital technologies are changing the way satellite systems operate.
3. Traditional satellite missions mainly collected data and transmitted it back to Earth for analysis.
4. However, new-generation systems increasingly incorporate intelligent processing capabilities directly onboard satellites.

## render category chars
{"document_context": 1447, "style": 66, "glossary": 907, "entities": 226, "neighbor_context": 770, "distant_context": 1531, "source": 501}
truncated: False