"""Capture the four workspaces and verify their shared desktop geometry.

Usage: python3 scripts/workspace_consistency_check.py --task TASK_ID
Read-only browser interactions: term selection, review filters, and More menus.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

import ui_screenshot

SCRIPT = r'''
const {chromium} = require(process.env.FOLIO_PLAYWRIGHT_CORE);
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.FOLIO_CHROME,args:['--no-sandbox']});
 const page=await browser.newPage({viewport:{width:1440,height:800},deviceScaleFactor:2});
 const [task,out]=process.argv.slice(2);
 const metrics=[];
 for(const width of [1280,1440,1600]) {
  await page.setViewportSize({width,height:800});
  for(const section of ['translation','terms','delivery','review']) {
   await page.goto(`http://127.0.0.1:8501/?task=${task}&section=${section}`);
   await page.locator('.tp-workspace-topbar-copy h1').waitFor();
   await page.waitForTimeout(1500);
   if(section==='terms') {
    const empty=page.locator('.tp-inspector-empty');
    if(width===1440) await page.screenshot({path:`${out}/terms-empty.png`});
    await page.locator('[class*="st-key-workspace_term_select_"] button:visible').filter({hasText:/^Germany$/}).click();
    await page.locator('.st-key-term_inspector h3').waitFor();
   }
   if(section==='review') {
    const all=page.locator('.st-key-review_queue_filters button').filter({hasText:/全部/});
    const expected=Number((await all.innerText()).match(/\d+/)[0]);
    await all.click();
    await page.waitForFunction(count=>document.querySelectorAll('.st-key-review_queue_list [data-testid="stRadioOption"]').length===count,expected,{timeout:15000});
   }
   await page.mouse.move(0,0);
   await page.screenshot({path:`${out}/final-${section}-${width}.png`});
   const geometry=await page.evaluate(()=>{
    const box=s=>{const e=document.querySelector(s); if(!e)return null;const r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom}};
    const title=document.querySelector('.tp-workspace-topbar-copy h1');
    const nav=document.querySelector('.st-key-workspace_nav_item_delivery_attention button');
    const inspector=box('.st-key-workspace_context_col');
    const buttons=[...document.querySelectorAll('[class*="st-key-cat_tools_"] button')].filter(e=>e.getBoundingClientRect().width>0);
    return {title:box('.tp-workspace-topbar-copy h1'),header:box('.st-key-workspace_exit_actions'),inspector,
      titleFits:title.scrollWidth<=title.clientWidth,
      visibleTranslationActions:buttons.length,
      termRow:box('[class*="st-key-workspace_term_row_"]'),
      reviewRow:box('.st-key-review_queue_list [data-testid="stRadioOption"]'),
      reviewQueue:box('.st-key-review_queue_list'),
      artifact:box('[class*="st-key-tp_card_prim_"]'),
      deliveryNavColor:nav?getComputedStyle(nav).color:null,
      horizontalOverflow:document.documentElement.scrollWidth>innerWidth};
   });
   if(geometry.title.y!==8 || !geometry.titleFits || geometry.horizontalOverflow) throw Error(`Shell overflow: ${section} ${width}`);
   if(geometry.inspector && Math.round(geometry.inspector.width)!==340) throw Error(`Inspector width: ${section} ${JSON.stringify(geometry)}`);
   if(section==='translation' && geometry.visibleTranslationActions!==3) throw Error('Expected three inline actions');
   if(section==='delivery' && geometry.artifact.width>341) throw Error('Artifact too wide');
   if(section==='review' && Math.abs(geometry.reviewRow.width-geometry.reviewQueue.width)>1) throw Error('Queue row does not fill list');
   metrics.push({section,width,...geometry});
   if(section==='translation' && width===1440) {
    await page.locator('[class*="st-key-cat_actions_"] button:visible').first().click();
    await page.locator('[class*="st-key-cat_action_source_"] button:visible').waitFor();
    await page.screenshot({path:`${out}/translation-more.png`});
   }
   if(section==='review' && width===1440) {
    const before=await page.locator('.st-key-review_inspector h3').innerText();
    await page.locator('.st-key-review_queue_list [data-testid="stRadioOption"]').nth(1).click();
    await page.waitForFunction(old=>document.querySelector('.st-key-review_inspector h3')?.textContent!==old,before);
    const after=await page.locator('.st-key-review_inspector h3').innerText();
    if(before===after) throw Error('Queue selection did not update Inspector');
   }
  }
 }
 require('fs').writeFileSync(`${out}/geometry.json`,JSON.stringify(metrics,null,2));
 console.log(JSON.stringify(metrics));
 await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
'''

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--task',required=True)
    parser.add_argument('--out',default='docs/ui-audit/workspace-consistency')
    args=parser.parse_args()
    Path(args.out).mkdir(parents=True,exist_ok=True)
    env={**os.environ,'FOLIO_PLAYWRIGHT_CORE':ui_screenshot._resolve_playwright(),
         'FOLIO_CHROME':ui_screenshot._resolve_chrome()}
    with tempfile.NamedTemporaryFile('w',suffix='.js') as script:
        script.write(SCRIPT);script.flush()
        result=subprocess.run(['node',script.name,args.task,args.out],env=env)
    raise SystemExit(result.returncode)
