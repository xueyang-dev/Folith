# dynamic_v2 packet inspection - offset 200, batch 4, budget 1800 tokens

index: {'indexed_segments': 239, 'term_keys': 15, 'entity_keys': 30, 'keyword_keys': 957, 'section_keys': 0}
section: None

## current batch
1. Traditional space missions have depended heavily on materials transported from Earth.
2. However, long-term operations require more sustainable approaches.
3. In-situ resource utilisation (ISRU) refers to the use of locally available materials to support exploration activities.
4. Potential applications include extracting water ice, producing oxygen, manufacturing construction materials and supporting life-support systems.

## query profile
terms: []
entities: ['Earth']
keywords: ['traditional', 'space', 'missions', 'depended', 'heavily', 'materials', 'transported', 'earth', 'however', 'long-term', 'operations', 'require', 'sustainable', 'approaches', 'in-situ', 'resource', 'utilisation', 'isru', 'refers', 'use', 'locally', 'available', 'support', 'exploration', 'activities', 'potential', 'applications', 'include', 'extracting', 'water', 'ice', 'producing', 'oxygen', 'manufacturing', 'construction', 'supporting', 'life-support', 'systems']

## candidate pool by source
{"rule": 1, "glossary": 15, "entity": 5, "distant_segment": 8, "summary": 3, "local_context": 5}

## selected context (rank order, score features)
- rule:style | rule | score=60.0 | tokens=14 | authority=project_confirmed_knowledge | features={'explicit_rule': 60.0}
- glossary:t-f5b520f9594b | glossary | score=41.0526 | tokens=11 | authority=automatic_observation | features={'lexical_overlap': 1.0526, 'shared_term': 40.0}
- glossary:t-ed042984ee10 | glossary | score=40.5263 | tokens=4 | authority=automatic_observation | features={'lexical_overlap': 0.5263, 'shared_term': 40.0}
- glossary:t-f40fd03d3ac3 | glossary | score=40.5263 | tokens=11 | authority=automatic_observation | features={'lexical_overlap': 0.5263, 'shared_term': 40.0}
- glossary:t-a8a95109fef7 | glossary | score=40.5263 | tokens=13 | authority=automatic_observation | features={'lexical_overlap': 0.5263, 'shared_term': 40.0}
- glossary:t-068af3acf604 | glossary | score=40.5263 | tokens=15 | authority=automatic_observation | features={'lexical_overlap': 0.5263, 'shared_term': 40.0}
- glossary:t-53fe57a066d0 | glossary | score=40.0 | tokens=4 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-c5ae7686eddc | glossary | score=40.0 | tokens=4 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-43efa5a7f62d | glossary | score=40.0 | tokens=5 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-4de404c77021 | glossary | score=40.0 | tokens=5 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-cbe41653671b | glossary | score=40.0 | tokens=5 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-7c8a3fe5a47a | glossary | score=40.0 | tokens=7 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-87ddcb5b3a3b | glossary | score=40.0 | tokens=7 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-92d4f3b7db80 | glossary | score=40.0 | tokens=9 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-ea4853018ab6 | glossary | score=40.0 | tokens=9 | authority=automatic_observation | features={'shared_term': 40.0}
- glossary:t-7896bada5f8b | glossary | score=40.0 | tokens=27 | authority=automatic_observation | features={'shared_term': 40.0}
- entity:entity-0003 | entity | score=30.5263 | tokens=10 | authority=automatic_observation | features={'lexical_overlap': 0.5263, 'shared_entity': 30.0}
- entity:entity-0002 | entity | score=30.5263 | tokens=15 | authority=automatic_observation | features={'lexical_overlap': 0.5263, 'shared_entity': 30.0}
- entity:entity-0001 | entity | score=30.0 | tokens=7 | authority=automatic_observation | features={'shared_entity': 30.0}
- entity:entity-0005 | entity | score=30.0 | tokens=9 | authority=automatic_observation | features={'shared_entity': 30.0}
- entity:entity-0012 | entity | score=30.0 | tokens=9 | authority=automatic_observation | features={'shared_entity': 30.0}
- distant:168 | distant_segment | score=30.2105 | tokens=72 | authority=project_confirmed_knowledge | features={'distance_penalty': -4.0, 'lexical_overlap': 4.2105, 'shared_entity': 30.0}
- distant:185 | distant_segment | score=29.7039 | tokens=40 | authority=project_confirmed_knowledge | features={'distance_penalty': -1.875, 'lexical_overlap': 1.5789, 'shared_entity': 30.0}
- summary:section:unit-0003 | summary | score=28.1579 | tokens=126 | authority=raw_distant_context | features={'document_context': 15.0, 'lexical_overlap': 3.1579, 'same_section': 10.0}
- local:next:1 | local_context | score=27.6316 | tokens=42 | authority=raw_distant_context | features={'direct_neighbour': 25.0, 'lexical_overlap': 2.6316}
- local:prev:1 | local_context | score=26.0526 | tokens=24 | authority=raw_distant_context | features={'direct_neighbour': 25.0, 'lexical_overlap': 1.0526}
- local:target:0 | local_context | score=26.0526 | tokens=36 | authority=automatic_observation | features={'direct_neighbour': 25.0, 'lexical_overlap': 1.0526}
- local:next:0 | local_context | score=25.5263 | tokens=20 | authority=raw_distant_context | features={'direct_neighbour': 25.0, 'lexical_overlap': 0.5263}
- local:prev:0 | local_context | score=25.5263 | tokens=32 | authority=raw_distant_context | features={'direct_neighbour': 25.0, 'lexical_overlap': 0.5263}
- summary:document | summary | score=15.5263 | tokens=137 | authority=raw_distant_context | features={'document_context': 15.0, 'lexical_overlap': 0.5263}
- summary:profile | summary | score=15.0 | tokens=45 | authority=raw_distant_context | features={'document_context': 15.0}
- distant:45 | distant_segment | score=12.2039 | tokens=33 | authority=project_confirmed_knowledge | features={'distance_penalty': -19.375, 'lexical_overlap': 1.5789, 'shared_entity': 30.0}
- distant:42 | distant_segment | score=11.8289 | tokens=37 | authority=project_confirmed_knowledge | features={'distance_penalty': -19.75, 'lexical_overlap': 1.5789, 'shared_entity': 30.0}
- distant:32 | distant_segment | score=11.6316 | tokens=48 | authority=project_confirmed_knowledge | features={'distance_penalty': -21.0, 'lexical_overlap': 2.6316, 'shared_entity': 30.0}
- distant:16 | distant_segment | score=10.1579 | tokens=64 | authority=project_confirmed_knowledge | features={'distance_penalty': -23.0, 'lexical_overlap': 3.1579, 'shared_entity': 30.0}
- distant:197 | distant_segment | score=2.2566 | tokens=57 | authority=project_confirmed_knowledge | features={'distance_penalty': -0.375, 'lexical_overlap': 2.6316}
- distant:184 | distant_segment | score=0.6316 | tokens=56 | authority=project_confirmed_knowledge | features={'distance_penalty': -2.0, 'lexical_overlap': 2.6316}

## selected tokens by source
{"rule": 14, "glossary": 136, "entity": 50, "distant_segment": 407, "summary": 308, "local_context": 154}
total selected tokens: 1069
rejected candidates: 0
consolidated duplicates: 7
priority (batch-matched constraints): 20
distant retrieved/selected: 8/8

## distant hits
- segment 168 distance 32 retrieval=entity terms=[] entities=['Earth'] keywords=['space', 'missions', 'earth', 'long-term', 'operations', 'sustainable']
- segment 185 distance 15 retrieval=entity terms=[] entities=['Earth'] keywords=['missions', 'earth', 'support']
- segment 45 distance 155 retrieval=entity terms=[] entities=['Earth'] keywords=['earth', 'potential', 'applications']
- segment 42 distance 158 retrieval=entity terms=[] entities=['Earth'] keywords=['traditional', 'missions', 'earth']
- segment 32 distance 168 retrieval=entity terms=[] entities=['Earth'] keywords=['space', 'earth', 'exploration', 'applications', 'include']
- segment 16 distance 184 retrieval=entity terms=[] entities=['Earth'] keywords=['space', 'earth', 'operations', 'exploration', 'applications', 'systems']
- segment 197 distance 3 retrieval=keyword terms=[] entities=[] keywords=['require', 'activities', 'supporting', 'life-support', 'systems']
- segment 184 distance 16 retrieval=keyword terms=[] entities=[] keywords=['long-term', 'resource', 'utilisation', 'exploration', 'systems']

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
- European Space Operations Centre -> 欧洲空间操作中心（ID: t-f5b520f9594b）
- New Space -> 新太空（ID: t-ed042984ee10）
- European Space Agency (ESA) -> 欧洲空间局（ESA）（ID: t-f40fd03d3ac3）
- uncrewed aircraft systems, or UAS -> 无人航空器系统（UAS）（ID: t-a8a95109fef7）
- OECD The Space Economy at a Glance 2026 -> OECD《2026年太空经济概览》（ID: t-068af3acf604）
- Germany -> 德国（ID: t-53fe57a066d0）
- Cologne -> 科隆（ID: t-c5ae7686eddc）
- Artemis -> 阿尔忒弥斯计划（ID: t-43efa5a7f62d）
- Darmstadt -> 达姆施塔特（ID: t-4de404c77021）
- UAM -> 城市空中交通（UAM）（ID: t-cbe41653671b）
- Uncrewed aviation -> 无人航空（ID: t-7c8a3fe5a47a）
- Urban Air Mobility -> 城市空中交通（ID: t-87ddcb5b3a3b）
- European Astronaut Centre -> 欧洲宇航员中心（ID: t-92d4f3b7db80）
- European lunar initiatives -> 欧洲月球倡议（ID: t-ea4853018ab6）
- European Organisation for the Exploitation of Meteorological Satellites (EUMETSAT) -> 欧洲气象卫星开发组织（EUMETSAT）（ID: t-7896bada5f8b）

【当前语义单元摘要】
本单元介绍欧洲服务舱（ESM）及其对关键功能的支持，并说明德国在相关部件研制中的作用；随后系统阐述未来月球探索所需的技术与模式，包括自主机器人、通信导航、能源、原位资源利用、商业参与和可持续治理，强调国际合作、德国技术能力以及从短期任务向长期月球开发的转变。

【当前单元翻译提示】
整体语气为客观、技术政策综述、宜用正式简体中文、避免口语化。术语需统一：ESA可统一为“欧洲空间局”或“欧洲航天局”、ESM首次出现时可处理为“ESM（欧洲服务舱）”、后文“European Service Module”统一为“欧洲服务舱”、“Artemis”译为“阿尔忒弥斯计划”时应确认上下文确指计划、“in-situ resource utilisation (ISRU)”译为“原位资源利用（ISRU）”、“life-support”统一为“生命保障”、“thermal control”译为“热控”、“permanently shadowed regions”译为“永久阴影区”、“abrasive dust”可译为“磨蚀性尘埃”或结合语境译为“研磨性月尘”、“space sustainability”译为“太空可持续性”

【全文概要】
材料围绕德国与欧洲的航天/太空发展以及城市空中交通与无人航空展开。第一部分概述德国在欧洲航天经济中的地位、商业航天与 New Space 趋势、与 ESA、欧盟空间计划、EUMETSAT、DLR 等机构和项目的合作，并涉及卫星服务、地球观测、卫星导航、商业发射、人工智能、星上智能处理、政府政策、监管与可持续性挑战；随后转向城市空中交通（UAM）与无人航空，介绍 eVTOL 技术基础、垂直起降设施、系统级研究和飞机设计挑战。第二部分继续讨论 eVTOL/UAM 的电池容量与重量权衡、推进架构、安全、自动化、空域整合、垂直起降设施、与现有交通系统的关系、货运与无人航空器系统应用，并涉及德国在 UAS 研究、公众接受、噪声、环境、经济性、监管与合作方面的路径；之后进入第三部分，转向德国通过 ESA、DLR 及航天企业参与 NASA 阿尔忒弥斯计划、月球探索与猎户座飞船欧洲服务舱等未来太空发展。第三部分介绍欧洲服务舱（ESM）及其对推进、发电、热控、水供应、生命保障等关键功能的支持，说明德国在相关部件研制中的作用；并系统阐述未来月球探索所需的技术与模式，包括自主机器人、通信导航、能源、原位资源利用、商业参与和可持续治理，强调国际合作、德国技术能力以及从短期任务向长期月球开发的转变。

【专名/实体连续性提示（仅作建议；人工实体选择优先）】
- Space Economy -> 太空经济（named_concept）
- The Space Economy at a Glance 2026 -> 《2026年太空经济概览》（book）
- OECD -> OECD（organization）
- ESA -> 欧洲空间局（ESA）（organization）
- EUMETSAT -> EUMETSAT（organization）

【前文原文上下文】
- The utilisation of local resources is also becoming a major focus of future lunar exploration.
- Research into advanced solar power systems, energy storage technologies and alternative power solutions is therefore essential.

【前文已接受译文连续性】
- 段 199 [reviewed] 原文：The utilisation of local resources is also becoming a major focus of future lunar exploration.
  译文：[accepted target 199]

【后文原文上下文】
- Water ice located in permanently shadowed regions could potentially be processed into oxygen and hydrogen, which may support human activities and spacecraft propulsion.
- The Moon’s resources may provide important opportunities for future exploration.

【远距离相关段落（历史上下文，仅供一致性参考）】
- [段 169 · 距离 32] 原文：After decades of scientific missions focused on understanding the Moon and preparing technologies for deep-space exploration, international space activities are increasingly moving toward long-term and sustainable operations beyond Earth.
  已接受译文：[accepted target 168]
- [段 186 · 距离 15] 原文：These technologies are essential because future missions cannot rely entirely on continuous support from Earth.
  已接受译文：[accepted target 185]
- [段 46 · 距离 155] 原文：Artificial intelligence has particular potential in Earth observation applications.
  已接受译文：[accepted target 45]
- [段 43 · 距离 158] 原文：Traditional satellite missions mainly collected data and transmitted it back to Earth for analysis.
  已接受译文：[accepted target 42]
- [段 33 · 距离 168] 原文：Major investment areas include Earth observation, launch technologies, space science, exploration programmes and satellite-related applications.
  已接受译文：[accepted target 32]
- [段 17 · 距离 184] 原文：The country has developed particular strengths in satellite systems, Earth observation, space operations, exploration technologies, launcher propulsion and commercial applications of space-based information.
  已接受译文：[accepted target 16]
- [段 198 · 距离 3] 原文：Future lunar facilities will require reliable energy systems capable of supporting scientific instruments, communication equipment, life-support systems and industrial activities.
  已接受译文：[accepted target 197]
- [段 185 · 距离 16] 原文：Long-term lunar exploration requires solutions in areas such as autonomous robotics, communication networks, energy systems, mobility technologies and resource utilisation.
  已接受译文：[accepted target 184]

【待翻译段落（按序号返回等长数组）】
1. Traditional space missions have depended heavily on materials transported from Earth.
2. However, long-term operations require more sustainable approaches.
3. In-situ resource utilisation (ISRU) refers to the use of locally available materials to support exploration activities.
4. Potential applications include extracting water ice, producing oxygen, manufacturing construction materials and supporting life-support systems.

## render category chars
{"document_context": 1257, "style": 66, "glossary": 907, "entities": 226, "neighbor_context": 655, "distant_context": 1668, "source": 448}
truncated: False