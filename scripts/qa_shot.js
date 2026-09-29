/*
 * HTML 交付物无头质检：结构统计 + 坏图检测 + 分章节截图。
 *
 * 用法（在 skill 目录下，用系统 Edge，无需下载浏览器）：
 *   mkdir -p "<out>/_qa"
 *   # 依赖 playwright-core；若未安装：npm i playwright-core（装到任意 NODE_PATH 可见处）
 *   node qa_shot.js
 *
 * 关键点：
 *  - imgsBroken 必须为空数组，这是 base64 拼错 / 路径错的唯一可靠探针
 *  - 用 channel:'msedge' 调系统 Edge，本机 playwright-core 已装但没下浏览器
 *  - 截图后自己要 Read 几张确认排版，别只看 JSON 就下结论
 */
const { chromium } = require('playwright-core');
const fs = require('fs');
const path = require('path');

// ==================== CONFIG ====================
const HTML_PATH = 'D:/path/to/方案报告.html';
const OUT_DIR = 'D:/path/to/_qa';
const SHOT_SECTIONS = ['#s3', '#s5', '#s7', '#s8', '#s10', '#sa'];  // 想盲检的章节 id
// ================================================

// 直接把本地路径转成 file:// URL，避免手写百分号编码出错
const fileUrl = 'file:///' + HTML_PATH.replace(/\\/g, '/').split('/')
  .map((s, i) => (i === 0 ? s : encodeURIComponent(s))).join('/');

(async () => {
  fs.mkdirSync(OUT_DIR, { recursive: true });

  let browser;
  try {
    browser = await chromium.launch({ channel: 'msedge' });
  } catch (e) {
    console.log('msedge 不可用，尝试 chrome: ' + e.message.split('\n')[0]);
    browser = await chromium.launch({ channel: 'chrome' });
  }

  const page = await browser.newPage({
    viewport: { width: 1280, height: 900 },
    deviceScaleFactor: 1,
  });

  const errs = [];
  page.on('pageerror', e => errs.push('pageerror: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });

  await page.goto(fileUrl, { waitUntil: 'load', timeout: 60000 });
  await page.waitForTimeout(1200);

  const info = await page.evaluate(() => {
    const imgs = [...document.images];
    return {
      title: document.title,
      docHeight: document.body.scrollHeight,
      sections: document.querySelectorAll('section').length,
      tables: document.querySelectorAll('table').length,
      figures: document.querySelectorAll('figure').length,
      imgsTotal: imgs.length,
      imgsBroken: imgs.filter(i => !i.complete || i.naturalWidth === 0).map(i => i.alt),
      headings: [...document.querySelectorAll('.sec-head h2')].map(e => e.textContent),
    };
  });

  console.log(JSON.stringify(info, null, 2));
  console.log('ERRORS:', errs.length ? errs : 'none');
  if (info.imgsBroken.length) console.log('>>> 警告：存在坏图，必须修复后再交付 <<<');

  await page.screenshot({ path: path.join(OUT_DIR, 'qa_cover.png') });
  for (const sel of SHOT_SECTIONS) {
    const el = await page.$(sel);
    if (el) await el.screenshot({ path: path.join(OUT_DIR, 'qa_' + sel.replace('#', '') + '.png') });
  }

  await browser.close();
  console.log('screenshots ->', OUT_DIR);
})().catch(e => { console.error('FAILED:', e.message); process.exit(1); });
