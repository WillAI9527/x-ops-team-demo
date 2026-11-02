const MON_EN = { '自有网站/博客': 'Own website / blog', '付费社群/订阅': 'Paid community / subscription', '课程/电子书/产品': 'Course / e-book / product', '推广/联盟链接': 'Promo / affiliate links', '广告/赞助合作': 'Ads / sponsorships', '交易所/币圈导流': 'Exchange / crypto referrals' };
// v1.4 对标账号分析 + 知识库 + 仿写 + 发帖队列 (uses helpers from index.html: $, esc, headers, md, rvBadge, rvDetails, wireRv, rvOpts, rvLive, isReviewEv, rvSumLine)
let bCur = null, bData = null, bCfg = {};
const L = (zh, en) => (window.LANG === 'en' ? en : zh);
const WZ = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'], WE = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
let W = WZ;
const HR = h => L(h + '点', h + ':00');
const bSRC = x => window.LANG === 'en' ? String(x || '').replace('（缓存）', ' (cached)').replace('（免密）', ' (keyless)') : String(x || '');
const AGN = { researcher: ['选题研究员', 'Researcher'], editor: ['内容编辑', 'Editor'], community: ['社区运营', 'Community'], data: ['数据分析', 'Data'], chief: ['负责人·主编', 'Chief editor'] };
const agName = a => AGN[a] ? L(AGN[a][0], AGN[a][1]) : a;
const TIP = f => { if (!f || !f.bestSlots) return (f && f.tip) || '';
  const sl = f.bestSlots.length ? f.bestSlots.map(SLOT).join(L('、', ', ')) : L('样本不足', 'not enough data'), hs = f.bestHours.length ? f.bestHours.map(h => window.LANG === 'en' ? String(h).replace('点', ':00') : h).join(L('、', ', ')) : L('样本不足', 'not enough data');
  let t = L(`按对标博主互动最高的时段发：${sl}；每天优先 ${hs}（北京时间）。`, `Post in the benchmark's highest-engagement slots: ${sl}; each day prefer ${hs} (Beijing time).`);
  if (f.myPerDay != null) t += L(` 你现在每天约 ${f.myPerDay} 条，对标约 ${f.benchPerDay} 条。`, ` You post about ${f.myPerDay}/day now; the benchmark posts about ${f.benchPerDay}/day.`);
  return t; };
const SLOT = s => window.LANG === 'en' ? String(s).replace(/周[一二三四五六日]/g, m => WE[WZ.indexOf(m)]) : s;
const fmtN = n => window.LANG === 'en' ? (n >= 1e6 ? (n / 1e6).toFixed(1) + 'M' : n >= 1e4 ? (n / 1e3).toFixed(1) + 'K' : String(n)) : (n >= 10000 ? (n / 10000).toFixed(1) + '万' : String(n));
const fmtT = iso => { const d = new Date(iso); return d.toLocaleString(window.LANG === 'en' ? 'en-GB' : 'zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', weekday: 'short', hour: '2-digit', minute: '2-digit' }); };
async function api(path, opt = {}) {
  const r = await fetch(path, { headers: headers(), ...opt });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
async function stream(path, body, onEv) {
  const resp = await fetch(path, { method: 'POST', headers: headers(), body: JSON.stringify({ ...body, stream: true }) });
  if (!resp.ok) { const e = await resp.json().catch(() => ({})); throw new Error(e.error || ('HTTP ' + resp.status)); }
  const reader = resp.body.getReader(); const dec = new TextDecoder(); let buf = '', last = null;
  while (true) {
    const { value, done } = await reader.read(); if (done) break;
    buf += dec.decode(value, { stream: true }); let i;
    while ((i = buf.indexOf('\n')) >= 0) { const line = buf.slice(0, i); buf = buf.slice(i + 1); if (!line.trim()) continue; const ev = JSON.parse(line); if (ev.event === 'done') last = ev; onEv(ev); }
  }
  return last;
}
async function benchOpen() {
  try { bCfg = await api('/api/bench/config'); } catch (e) { $('#bCfg').textContent = L('读取配置失败：', 'Could not read the config: ') + e.message; return; }
  $('#bCfg').innerHTML = (bCfg.bearerConfigured ? L('✅ 已配置 X_BEARER_TOKEN（优先用 X API，失败自动改用免密公开接口）', '✅ X_BEARER_TOKEN configured (X API first, falls back to the keyless public endpoint)') : L('✅ 免密抓取：用公开接口 ', '✅ Keyless fetch: the public endpoint ') + esc(bCfg.keylessFetch || '') + L(' 读取任意公开账号（约最近 100–200 条原创帖）；配置 X_BEARER_TOKEN 可抓更多', ' reads any public account (about the latest 100–200 original posts); set X_BEARER_TOKEN to fetch more')) +
    ' ｜ ' + (bCfg.postKeysConfigured ? L('✅ 已配置发帖密钥', '✅ Posting keys configured') : L('⚠️ 未配置发帖密钥（到点会提示你用 x.com 手动发）', '⚠️ No posting keys (you will be asked to post manually on x.com when due)')) + L(' ｜ 时区：', ' ｜ Time zone: ') + esc(bCfg.tz);
  $('#bSample').innerHTML = `<option value="">${L('载入示例数据…', 'Load sample data…')}</option>` + (bCfg.samples || []).map(s => `<option value="${esc(s)}">@${esc(s)}${L('（真实公开数据）', ' (real public data)')}</option>`).join('');
  loadBList(); loadQueue();
}
async function loadBList() {
  const j = await api('/api/bench');
  $('#bList').innerHTML = j.accounts.length ? '' : `<span class="hint">${L('还没有', 'None yet')}</span>`;
  for (const a of j.accounts) {
    const b = document.createElement('button'); b.className = 'mode' + (a.username === bCur ? ' active' : '');
    b.innerHTML = '@' + esc(a.username) + `<small>${a.posts} ${L('帖', 'posts')} · ${a.analyzed_at ? L('已分析', 'analyzed') : L('未分析', 'not analyzed')}</small>`;
    b.onclick = () => openBench(a.username); $('#bList').appendChild(b);
  }
}
function prog(msg, spin = true) { $('#bProg').innerHTML = (spin ? '<span class="spin"></span>' : '') + esc(msg); }
async function runAnalyze(u) {
  bCur = u; loadBList();
  const t0 = Date.now();
  try {
    const r = await stream(`/api/bench/${u}/analyze`, {}, ev => { if (ev.event === 'step') prog(ev.msg + L(`（已用 ${((Date.now() - t0) / 1000).toFixed(0)}s）`, ` (${((Date.now() - t0) / 1000).toFixed(0)}s)`)); });
    if (!r || !r.ok) throw new Error((r && r.error) || L('分析失败', 'Analysis failed'));
    if (r.analysis) { try { localStorage.setItem('opsc:an:' + u.toLowerCase() + ':' + (window.LANG === 'en' ? 'en' : 'zh'), JSON.stringify({ analysis: r.analysis, kb: r.kb })); } catch (e) {} }
    prog(L('分析完成 · ', 'Analysis done · ') + ((Date.now() - t0) / 1000).toFixed(0) + 's', false);
  } catch (e) { prog(L('分析失败：', 'Analysis failed: ') + e.message, false); }
  openBench(u);
}
$('#bFetch').onclick = async () => {
  const u = $('#bUser').value.trim().replace(/^@/, '').replace(/^https?:\/\/(x|twitter)\.com\//, '').split(/[/?]/)[0];
  if (!/^[A-Za-z0-9_]{1,15}$/.test(u)) { $('#bUser').focus(); return; }
  $('#bFetch').disabled = true;
  try { prog('正在抓取 @' + u + ' 的公开资料和最近帖子（免密，约 10–30 秒）…'); const r = await api('/api/bench/fetch', { method: 'POST', body: JSON.stringify({ username: u }) });
    prog(`抓到 ${r.posts} 条，开始分析…`); await runAnalyze(r.username);
  } catch (e) { prog(L('抓取失败：', 'Fetch failed: ') + e.message, false); const rb = document.createElement('button'); rb.className = 'ghost sm'; rb.style.marginLeft = '8px'; rb.textContent = L('重试', 'Retry'); rb.onclick = () => $('#bFetch').click(); $('#bProg').appendChild(rb); } finally { $('#bFetch').disabled = false; }
};
$('#bFile').onchange = async e => {
  const f = e.target.files[0]; if (!f) return;
  try { const data = JSON.parse(await f.text()); prog('导入中…');
    const r = await api('/api/bench/import', { method: 'POST', body: JSON.stringify({ data }) });
    prog(`已导入 @${r.username}：${r.posts} 条，开始分析…`); await runAnalyze(r.username);
  } catch (err) { prog('导入失败：' + err.message, false); } e.target.value = '';
};
$('#bSample').onchange = async e => {
  const s = e.target.value; if (!s) return;
  try { const r = await api('/api/bench/import', { method: 'POST', body: JSON.stringify({ sample: s }) });
    prog(`已载入示例 @${r.username}：${r.posts} 条，开始分析…`); await runAnalyze(r.username);
  } catch (err) { prog('载入失败：' + err.message, false); } e.target.value = '';
};
async function openBench(u) {
  bCur = u; loadBList(); location.hash = 'bench/' + u;
  bData = await api('/api/bench/' + u);
  // serverless: another instance may not have this account's analysis yet -> hand back the browser's copy
  const ck = 'opsc:an:' + u.toLowerCase() + ':' + (window.LANG === 'en' ? 'en' : 'zh');
  if (!bData.analysis) { try { const c = JSON.parse(localStorage.getItem(ck) || 'null'); const doc = JSON.parse(localStorage.getItem('opsc:doc:' + u.toLowerCase()) || 'null');
    if (c && doc) { await api('/api/bench/import', { method: 'POST', body: JSON.stringify({ data: doc, analysis: c.analysis, kb: c.kb, lang: window.LANG === 'en' ? 'en' : 'zh' }) }); bData = await api('/api/bench/' + u); } } catch (e) {} }
  if (bData.analysis) { try { localStorage.setItem(ck, JSON.stringify({ analysis: bData.analysis, kb: bData.kb })); } catch (e) {} }
  renderBench();
}
function kpi(label, val, sub) { return `<div class="kpi"><span>${esc(label)}</span><b>${val}</b>${sub ? `<span>${sub}</span>` : ''}</div>`; }
function renderBench() {
  const d = bData, u = d.user, an = d.analysis;
  let h = `<div class="panel"><div style="display:flex;gap:12px;align-items:center">${u.profile_image_url ? `<img src="${esc(u.profile_image_url)}" style="width:48px;height:48px;border-radius:50%">` : ''}
    <div><b style="font-size:16px" data-no-i18n>${esc(u.name)}</b> <a href="https://x.com/${esc(u.username)}" target="_blank" class="hint">@${esc(u.username)}</a>
    <div class="hint" data-no-i18n>${esc(u.description || '')}</div><div class="hint" data-no-i18n>${L('数据来源：', 'Source: ')}${esc(bSRC(d.source))} · ${d.postCount} ${L('条原创帖', 'original posts')} · ${L('抓取于', 'fetched')} ${esc(d.fetched_at || '')}</div></div>
    <div style="margin-left:auto"><button class="copy" id="bReAn">${an ? L('重新分析', 'Re-analyze') : L('开始分析', 'Analyze')}</button></div></div></div>`;
  if (!an) { $('#bOut').innerHTML = h; $('#bReAn').onclick = () => runAnalyze(u.username); return; }
  const st = an.stats, f = st.fan, fr = st.frequency;
  h += `<div class="panel"><b>👥 ${L('粉丝指数', 'Audience index')}</b><div class="bgrid" style="margin-top:8px">
    ${kpi(L('粉丝指数（0–100）', 'Audience index (0–100)'), f.fanIndex, L('粉丝规模 60% + 浏览互动率 40%', '60% follower scale + 40% engagement per view'))}${kpi(L('粉丝', 'Followers'), fmtN(f.followers), L('关注 ', 'Following ') + fmtN(f.following) + L(' · 比 ', ' · ratio ') + f.ratio)}
    ${kpi(L('平均浏览/帖', 'Avg views / post'), fmtN(Math.round(f.avg.views)))}${kpi(L('平均互动/帖', 'Avg interactions / post'), f.avgInteractions, '❤' + f.avg.likes + ' 🔁' + f.avg.reposts + ' 💬' + f.avg.replies)}
    ${kpi(L('互动率（按浏览）', 'Engagement rate (per view)'), f.erPerView + '%')}${kpi(L('互动率（按粉丝）', 'Engagement rate (per follower)'), f.erPerFollower + '%')}${kpi(L('平均收藏/帖', 'Avg bookmarks / post'), f.avg.bookmarks)}
    ${kpi(L('带图/视频帖热度', 'Heat of posts with media'), st.format.withMediaAvg, L('纯文字 ', 'Text only ') + st.format.textOnlyAvg)}</div></div>`;
  // topics
  const maxS = Math.max(1, ...an.topics.map(t => t.avgScore));
  h += `<div class="panel"><b>🔥 ${L('主要方向（按平均热度排序）', 'Main themes (by average heat)')}</b>${an.topics.length ? '' : '<div class="hint">' + L('主题聚类失败：', 'Topic clustering failed: ') + esc((an.errors || []).join('；')) + '</div>'}
    <table style="width:100%;font-size:13px;margin-top:8px;border-collapse:collapse">${an.topics.map((t, i) => `<tr style="border-top:1px solid var(--line)"><td style="padding:6px 4px;width:28px">${i + 1}</td>
    <td style="padding:6px 4px"><b data-no-i18n>${esc(t.name)}</b><div class="hint" data-no-i18n>${esc(t.desc)}</div><div class="hint">${L('钩子：', 'Hook: ')}<span data-no-i18n>${esc(t.hook || '')}</span></div></td>
    <td style="width:220px"><div class="tbar" style="width:${(100 * t.avgScore / maxS).toFixed(0)}%"></div><span class="hint">${L(`平均热度 ${t.avgScore} · ${t.count} 帖（${t.share}%）· 均浏览 ${fmtN(t.avgViews)}`, `Avg heat ${t.avgScore} · ${t.count} posts (${t.share}%) · avg views ${fmtN(t.avgViews)}`)}</span></td></tr>`).join('')}</table></div>`;
  // frequency + heatmap
  W = window.LANG === 'en' ? WE : WZ;
  const mx = Math.max(1, ...fr.heatmap.flat().map(c => c.avg));
  h += `<div class="panel"><b>⏰ ${L('互动频率与最佳时段（北京时间）', 'Posting frequency & best times (Beijing time)')}</b><div class="hint" style="margin:6px 0">${L(`每天约 ${fr.perDay} 条 · 每周约 ${fr.perWeek} 条 · 统计 ${st.spanDays} 天。`, `About ${fr.perDay} posts/day · ${fr.perWeek} posts/week · over ${st.spanDays} days.`)}
    ${L('最佳时段：', 'Best slots: ')}${fr.bestSlots.slice(0, 5).map(s => `<b>${esc(SLOT(s.label))}</b>${L(`（均热度 ${s.avgScore}，${s.posts} 帖）`, ` (avg heat ${s.avgScore}, ${s.posts} posts)`)}`).join(L('、', ', ')) || L('样本不足', 'not enough data')}${L('；最佳小时：', '; best hours: ')}${fr.bestHours.map(x => HR(x.hour)).join(L('、', ', ')) || L('样本不足', 'not enough data')}</div>
    <div style="overflow-x:auto"><table class="heat"><tr><th></th>${[...Array(24).keys()].map(x => `<th>${x}</th>`).join('')}</tr>
    ${fr.heatmap.map((row, di) => `<tr><th>${W[di]}</th>${row.map((c, hi) => `<td title="${W[di]} ${L(`${hi}点：${c.n} 帖，平均热度 ${c.avg}`, `${hi}:00 — ${c.n} posts, avg heat ${c.avg}`)}" style="background:${c.n ? `rgba(79,140,255,${(0.15 + 0.85 * c.avg / mx).toFixed(2)})` : 'transparent'};color:${c.n ? '#fff' : 'var(--muted)'}">${c.n || ''}</td>`).join('')}</tr>`).join('')}</table></div>
    <div class="hint">${L('格子数字 = 发帖数，颜色越深 = 平均热度越高（热度 = 赞 + 2×转发 + 1.5×回复 + 2×引用 + 0.5×收藏）', 'Number = posts; darker = higher average heat (heat = likes + 2×reposts + 1.5×replies + 2×quotes + 0.5×bookmarks)')}</div></div>`;
  // monetization
  const mo = an.monetization;
  h += `<div class="panel"><b>💰 ${L('变现信号（代码检测）', 'Monetization signals (rule-based)')}</b><div class="hint" style="margin:6px 0">${L('主页链接：', 'Profile link: ')}${esc(mo.bioLink || L('无', 'none'))}${L(` · 带外链的帖子 ${mo.postsWithLinks} 条 · 常见外链域名：`, ` · posts with external links: ${mo.postsWithLinks} · common domains: `)}${mo.topDomains.map(x => esc(x[0]) + '×' + x[1]).join(L('、', ', ')) || L('无', 'none')}</div>
    ${mo.signals.map(s => `<span class="pill">${esc(L(s.type, MON_EN[s.type] || s.type))} ×${s.count}</span>`).join('') || `<span class="hint">${L('未检测到明显变现信号', 'No clear monetization signals found')}</span>`}</div>`;
  if (an.forMe) h += `<div class="panel"><b>🎯 ${L('对你的账号', 'For your account')}${esc(acctLabel(an.forMe.account || window.ACCOUNT, ' '))}${L('：发帖时间建议', ': posting-time tips')}</b><div style="margin-top:6px">${esc(TIP(an.forMe))}</div><div class="hint">${L('「该学什么」见下方报告最后两节；发帖队列会自动排到这些时段。', '"What to learn" is in the last two sections of the report below; the posting queue schedules into these slots automatically.')}</div></div>`;
  h += `<div class="panel"><b>🧠 ${L('分析报告（Claude 主编）', 'Analysis report (Claude, chief editor)')}</b><div class="md" style="margin-top:6px">${an.report ? md(an.report) : '<span class="hint">' + L('报告生成失败：', 'Report failed: ') + esc((an.errors || []).join('；')) + '</span>'}</div></div>`;
  // knowledge base
  const kb = d.kb || { items: [] };
  h += `<div class="panel"><div style="display:flex;justify-content:space-between;align-items:center"><b>📚 ${L(`爆款知识库（热度前 ${kb.topPercent || 20}% · ${kb.items.length} 条，文字和图片已存到当前账号的本地目录 `, `Viral library (top ${kb.topPercent || 20}% by heat · ${kb.items.length} posts; text and images saved locally under `)}<span data-no-i18n>bench/${esc(u.username.toLowerCase())}/kb/</span>${L('）', ')')}</b>
    <button class="copy" id="bKbRe">${L('重新下载', 'Re-download')}</button></div>
    <div class="row"><input id="bKbQ" placeholder="${L('搜索关键词', 'Search keywords')}" style="flex:1"><select id="bKbT" data-no-i18n><option value="">${L('全部主题', 'All themes')}</option>${[...new Set(kb.items.map(x => x.topic).filter(Boolean))].map(t => `<option>${esc(t)}</option>`).join('')}</select></div>
    <div id="bKbList" style="margin-top:10px"></div></div><div id="bImit"></div>`;
  $('#bOut').innerHTML = h;
  $('#bReAn').onclick = () => runAnalyze(u.username);
  $('#bKbRe').onclick = async () => { prog(L('重新下载知识库…', 'Re-downloading the library…')); try { await api(`/api/bench/${u.username}/kb`, { method: 'POST', body: '{}' }); prog(L('知识库已更新', 'Library updated'), false); openBench(u.username); } catch (e) { prog('失败：' + e.message, false); } };
  const renderKb = () => {
    const q = $('#bKbQ').value.trim().toLowerCase(), tp = $('#bKbT').value;
    const items = kb.items.filter(x => (!tp || x.topic === tp) && (!q || x.text.toLowerCase().includes(q)));
    $('#bKbList').innerHTML = items.map(x => `<div class="kbitem"><div class="hint">#${x.rank} · <span data-no-i18n>${esc(x.topic || L('未分类', 'Uncategorized'))}</span> · ${esc(x.local_time)} · ${L('热度', 'Heat')} ${x.score} · ❤${x.metrics.likes} 🔁${x.metrics.reposts} 💬${x.metrics.replies} 👁${fmtN(x.metrics.views)} · <a href="${esc(x.url)}" target="_blank">${L('原帖', 'Original')}</a></div>
      <div class="raw-post" data-no-i18n lang="" style="white-space:pre-wrap;margin-top:6px;font-size:14px">${esc(x.text)}</div>
      <div>${x.media.filter(m => m.saved).map(m => `<a href="/api/bench/${esc(u.username)}/media/${esc(m.file)}" target="_blank"><img src="/api/bench/${esc(u.username)}/media/${esc(m.file)}" loading="lazy" title="${m.type === 'video' ? L('视频封面', 'Video cover') : L('图片', 'Image')}"></a>`).join('')}</div>
      <div class="row"><button class="run" data-imit="${esc(x.id)}" style="padding:6px 12px;font-size:13px">${L('让 5 个角色仿写这条', 'Have the 5 roles write a version of this')}${window.ACCOUNT && !isDefaultAcct(window.ACCOUNT) ? ' → @' + esc(window.ACCOUNT) : ''}</button></div></div>`).join('') || `<div class="hint">${L('没有匹配的帖子', 'No matching posts')}</div>`;
    $('#bKbList').querySelectorAll('[data-imit]').forEach(b => b.onclick = () => runImitate(u.username, b.dataset.imit, b));
  };
  $('#bKbQ').oninput = renderKb; $('#bKbT').onchange = renderKb; renderKb();
}
async function runImitate(u, pid, btn) {
  const box = $('#bImit'); btn.disabled = true;
  box.innerHTML = `<div class="panel"><b>✍️ 仿写结果</b>（原帖 #${esc((bData.kb.items.find(x => x.id === pid) || {}).rank + '')}）<span class="hint" id="bImLive"></span><div id="bImCards" style="margin-top:8px"></div></div>`;
  box.scrollIntoView({ behavior: 'smooth' });
  const cards = {};
  const ensure = ev => cards[ev.agent] || (cards[ev.agent] = (() => { const el = document.createElement('div'); el.className = 'card';
    el.innerHTML = `<div class="head"><div><span class="title">${esc(ev.name)}</span><span class="badge">${esc(ev.model || '')}</span></div><span class="status"><span class="spin"></span>生成中…</span></div><div class="body hint">生成中…</div>`; $('#bImCards').appendChild(el); return el; })());
  try {
    const r = await stream(`/api/bench/${u}/imitate`, { postId: pid, ...rvOpts() }, ev => {
      if (isReviewEv(ev)) { rvLive(ev); const el = ensure(ev); if (ev.event !== 'review') el.querySelector('.status').innerHTML = '<span class="spin"></span>' + (ev.event === 'fix' ? '按审查意见重写…' : '审查中…'); return; }
      if (ev.event === 'start') ensure(ev);
      if (ev.event === 'agent') {
        const el = ensure(ev), st = el.querySelector('.status'), body = el.querySelector('.body');
        if (!ev.ok) { st.className = 'status err'; st.textContent = '失败'; body.textContent = '出错：' + (ev.error || ''); return; }
        st.className = 'status ok'; st.textContent = '完成 · ' + (ev.ms / 1000).toFixed(0) + 's';
        body.className = 'body'; body.innerHTML = `<textarea class="bPost" style="min-height:110px">${esc(ev.post)}</textarea><div class="hint wl">X 加权 ${ev.weighted}/280</div>
          <details><summary class="hint">完整输出</summary><div style="white-space:pre-wrap">${esc(ev.output)}</div></details>
          <div class="row"><select class="bMode"><option value="review">审核后发</option><option value="auto">全自动发</option></select><button class="copy bQ">加入发帖队列（自动排最佳时段）</button><a class="copy bIntent" target="_blank" href="${esc(ev.intent)}">用 x.com 打开发帖框</a></div>`;
        if (ev.review) { el.querySelector('.title').insertAdjacentHTML('afterend', rvBadge(ev.review)); el.insertAdjacentHTML('beforeend', rvDetails(ev.review)); wireRv(el); }
        const ta = body.querySelector('.bPost');
        ta.oninput = () => { body.querySelector('.bIntent').href = 'https://x.com/intent/post?text=' + encodeURIComponent(ta.value); };
        body.querySelector('.bQ').onclick = async e => { try { const j = await api('/api/queue', { method: 'POST', body: JSON.stringify({ text: ta.value, mode: body.querySelector('.bMode').value, source: { username: u, postId: pid, agent: ev.agent } }) });
          e.target.textContent = '已排到 ' + fmtT(j.item.scheduled_at); e.target.disabled = true; renderQueue(j.queue); } catch (err) { alert(err.message); } };
      }
    });
    if (r && r.review) $('#bImLive').textContent = ' · ' + rvSumLine(r.review);
    if (r && r.ok === false && r.error) $('#bImLive').textContent = ' · 失败：' + r.error;
  } catch (e) { $('#bImLive').textContent = ' · 失败：' + e.message; }
  finally { btn.disabled = false; $('#rvLive').textContent = ''; }
}
// ---------------- queue ----------------
const QSZ = { pending: '待发', approved: '已审核·待到点', posted: '已发布', failed: '失败', manual: '需手动发', canceled: '已取消' };
const QSE = { pending: 'Pending', approved: 'Approved · waiting', posted: 'Posted', failed: 'Failed', manual: 'Post manually', canceled: 'Canceled' };
let lastQ = null;
async function loadQueue() { try { renderQueue(await api('/api/queue')); } catch (e) { $('#bQueue').textContent = L('读取队列失败：', 'Could not load the queue: ') + e.message; } }
function renderQueue(q) {
  lastQ = q; const QS = window.LANG === 'en' ? QSE : QSZ;
  $('#bQCfg').innerHTML = q.postKeysConfigured ? L(`✅ 已配置发帖密钥：到点后「全自动发」直接发；「审核后发」要你先点「审核通过」。调度器每 ${q.schedulerIntervalSec}s 检查一次（工具要保持运行）。`, `✅ Posting keys configured: "Auto-post" items go out when due; "Review first" items need "Approve" first. The scheduler checks every ${q.schedulerIntervalSec}s (keep the tool running).`)
    : L('⚠️ <b>未配置发帖密钥</b>（.env.local 里的 X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET）。到点后会标成「需手动发」，点「手动发」用 x.com 打开发帖框。', '⚠️ <b>No posting keys</b> (X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET in .env.local). When due, items are marked "Post manually"; click "Post manually" to open the x.com composer.');
  $('#bQueue').innerHTML = q.items.map(x => `<div class="qitem" data-id="${x.id}"><div class="hint"><span class="pill ${x.status}">${QS[x.status] || x.status}</span><span class="pill">${x.mode === 'auto' ? L('全自动发', 'Auto-post') : L('审核后发', 'Review first')}</span>
    ${fmtT(x.scheduled_at)}${L('（北京时间）· 加权 ', ' (Beijing time) · weighted ')}${x.weighted}/280 ${x.source && x.source.username ? L('· 仿写自 @', '· modeled on @') + esc(x.source.username) + (x.source.agent ? ' · ' + esc(agName(x.source.agent)) : '') : ''}
    ${x.tweetId ? ` · <a href="https://x.com/i/status/${esc(x.tweetId)}" target="_blank">${L('查看已发帖子', 'View post')}</a>` : ''}${x.error ? ` · <span style="color:var(--err)">${esc(x.error)}</span>` : ''}</div>
    <div class="raw-post" data-no-i18n lang="" style="white-space:pre-wrap;margin:6px 0;font-size:14px">${esc(x.text)}</div>
    ${x.status === 'posted' || x.status === 'canceled' ? `<div class="row" style="margin-top:0"><button class="copy" data-a="delete">${L('删除', 'Delete')}</button></div>` : `<div class="row" style="margin-top:0">
      ${x.mode === 'review' && x.status !== 'approved' ? `<button class="copy" data-a="approve">${L('审核通过', 'Approve')}</button>` : ''}
      <button class="copy" data-a="toggle">${L('改成', 'Switch to ')}${x.mode === 'auto' ? L('审核后发', 'review first') : L('全自动发', 'auto-post')}</button>
      ${q.postKeysConfigured ? `<button class="copy" data-a="post_now">${L('立即发', 'Post now')}</button>` : `<a class="copy" target="_blank" href="${esc(x.intent)}">${L('手动发', 'Post manually')}</a><button class="copy" data-a="mark_posted">${L('我已手动发好', 'I posted it')}</button>`}
      <button class="copy" data-a="edit">${L('编辑', 'Edit')}</button><button class="copy" data-a="cancel">${L('取消', 'Cancel')}</button></div>`}</div>`).join('') || `<div class="hint">${L('队列是空的。在知识库里点「让 5 个角色仿写」，再把满意的稿子加入队列。', 'The queue is empty. In the library, click "Have the 5 roles write a version", then add the drafts you like.')}</div>`;
  $('#bQueue').querySelectorAll('[data-a]').forEach(b => b.onclick = async () => {
    const id = b.closest('.qitem').dataset.id, x = q.items.find(i => i.id === id); let body = { action: b.dataset.a };
    if (b.dataset.a === 'toggle') body = { action: 'update', mode: x.mode === 'auto' ? 'review' : 'auto' };
    if (b.dataset.a === 'edit') { const t = prompt(L('编辑帖子内容', 'Edit the post'), x.text); if (t == null) return; body = { action: 'update', text: t }; }
    if (b.dataset.a === 'post_now' && !confirm(L('现在就用', 'Post this now from ') + acctLabel(window.ACCOUNT, ' ', L('你的账号', 'your account')) + L(' 发出这条帖子？', '?'))) return;
    try { renderQueue((await api('/api/queue/' + id, { method: 'POST', body: JSON.stringify(body) })).queue); } catch (e) { alert(e.message); }
  });
}
$('#bQRefresh').onclick = loadQueue;
$('#bQAdd').onclick = async () => {
  const text = $('#bQText').value.trim(); if (!text) return;
  const t = $('#bQTime').value;
  try { const j = await api('/api/queue', { method: 'POST', body: JSON.stringify({ text, mode: $('#bQMode').value, scheduled_at: t ? t + ':00+08:00' : undefined, source: bCur ? { username: bCur } : {} }) });
    $('#bQText').value = ''; renderQueue(j.queue); } catch (e) { alert(e.message); }
};
setInterval(() => { if (!$('#tab-bench').classList.contains('hidden')) loadQueue(); }, 30000);
if (location.hash.startsWith('#bench')) { const m = location.hash.match(/^#bench\/([A-Za-z0-9_]+)$/); document.querySelector('.tab[data-tab=bench]').click(); if (m) openBench(m[1]); }

// live language switch: re-render the dynamic bench views
document.querySelectorAll('[data-ui-language]').forEach(b => b.addEventListener('click', () => setTimeout(() => {
  W = window.LANG === 'en' ? WE : WZ;
  if (bCfg && Object.keys(bCfg).length) benchOpen();
  if (bData) renderBench();
  if (lastQ) renderQueue(lastQ);
}, 0)));
