// v1.4 对标账号分析 + 知识库 + 仿写 + 发帖队列 (uses helpers from index.html: $, esc, headers, md, rvBadge, rvDetails, wireRv, rvOpts, rvLive, isReviewEv, rvSumLine)
let bCur = null, bData = null, bCfg = {};
const W = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'];
const fmtN = n => n >= 10000 ? (n / 10000).toFixed(1) + '万' : String(n);
const fmtT = iso => { const d = new Date(iso); return d.toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', month: '2-digit', day: '2-digit', weekday: 'short', hour: '2-digit', minute: '2-digit' }); };
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
  try { bCfg = await api('/api/bench/config'); } catch (e) { $('#bCfg').textContent = '读取配置失败：' + e.message; return; }
  $('#bCfg').innerHTML = (bCfg.bearerConfigured ? '✅ 已配置 X_BEARER_TOKEN（优先用 X API，失败自动改用免密公开接口）' : '✅ 免密抓取：用公开接口 ' + esc(bCfg.keylessFetch || '') + ' 读取任意公开账号（约最近 100–200 条原创帖）；配置 X_BEARER_TOKEN 可抓更多') +
    ' ｜ ' + (bCfg.postKeysConfigured ? '✅ 已配置发帖密钥' : '⚠️ 未配置发帖密钥（到点会提示你用 x.com 手动发）') + ' ｜ 时区：' + esc(bCfg.tz);
  $('#bSample').innerHTML = '<option value="">载入示例数据…</option>' + (bCfg.samples || []).map(s => `<option value="${esc(s)}">@${esc(s)}（真实公开数据）</option>`).join('');
  loadBList(); loadQueue();
}
async function loadBList() {
  const j = await api('/api/bench');
  $('#bList').innerHTML = j.accounts.length ? '' : '<span class="hint">还没有</span>';
  for (const a of j.accounts) {
    const b = document.createElement('button'); b.className = 'mode' + (a.username === bCur ? ' active' : '');
    b.innerHTML = '@' + esc(a.username) + `<small>${a.posts} 帖 · ${a.analyzed_at ? '已分析' : '未分析'}</small>`;
    b.onclick = () => openBench(a.username); $('#bList').appendChild(b);
  }
}
function prog(msg, spin = true) { $('#bProg').innerHTML = (spin ? '<span class="spin"></span>' : '') + esc(msg); }
async function runAnalyze(u) {
  bCur = u; loadBList();
  const t0 = Date.now();
  try {
    const r = await stream(`/api/bench/${u}/analyze`, {}, ev => { if (ev.event === 'step') prog(ev.msg + `（已用 ${((Date.now() - t0) / 1000).toFixed(0)}s）`); });
    if (!r || !r.ok) throw new Error((r && r.error) || '分析失败');
    prog('分析完成 · ' + ((Date.now() - t0) / 1000).toFixed(0) + 's', false);
  } catch (e) { prog('分析失败：' + e.message, false); }
  openBench(u);
}
$('#bFetch').onclick = async () => {
  const u = $('#bUser').value.trim().replace(/^@/, '').replace(/^https?:\/\/(x|twitter)\.com\//, '').split(/[/?]/)[0];
  if (!/^[A-Za-z0-9_]{1,15}$/.test(u)) { $('#bUser').focus(); return; }
  $('#bFetch').disabled = true;
  try { prog('正在抓取 @' + u + ' 的公开资料和最近帖子（免密，约 10–30 秒）…'); const r = await api('/api/bench/fetch', { method: 'POST', body: JSON.stringify({ username: u }) });
    prog(`抓到 ${r.posts} 条，开始分析…`); await runAnalyze(r.username);
  } catch (e) { prog('抓取失败：' + e.message, false); } finally { $('#bFetch').disabled = false; }
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
  renderBench();
}
function kpi(label, val, sub) { return `<div class="kpi"><span>${esc(label)}</span><b>${val}</b>${sub ? `<span>${sub}</span>` : ''}</div>`; }
function renderBench() {
  const d = bData, u = d.user, an = d.analysis;
  let h = `<div class="panel"><div style="display:flex;gap:12px;align-items:center">${u.profile_image_url ? `<img src="${esc(u.profile_image_url)}" style="width:48px;height:48px;border-radius:50%">` : ''}
    <div><b style="font-size:16px">${esc(u.name)}</b> <a href="https://x.com/${esc(u.username)}" target="_blank" class="hint">@${esc(u.username)}</a>
    <div class="hint">${esc(u.description || '')}</div><div class="hint">数据来源：${esc(d.source || '')} · ${d.postCount} 条原创帖 · 抓取于 ${esc(d.fetched_at || '')}</div></div>
    <div style="margin-left:auto"><button class="copy" id="bReAn">${an ? '重新分析' : '开始分析'}</button></div></div></div>`;
  if (!an) { $('#bOut').innerHTML = h; $('#bReAn').onclick = () => runAnalyze(u.username); return; }
  const st = an.stats, f = st.fan, fr = st.frequency;
  h += `<div class="panel"><b>👥 粉丝指数</b><div class="bgrid" style="margin-top:8px">
    ${kpi('粉丝指数（0–100）', f.fanIndex, '粉丝规模 60% + 浏览互动率 40%')}${kpi('粉丝', fmtN(f.followers), '关注 ' + fmtN(f.following) + ' · 比 ' + f.ratio)}
    ${kpi('平均浏览/帖', fmtN(Math.round(f.avg.views)))}${kpi('平均互动/帖', f.avgInteractions, '❤' + f.avg.likes + ' 🔁' + f.avg.reposts + ' 💬' + f.avg.replies)}
    ${kpi('互动率（按浏览）', f.erPerView + '%')}${kpi('互动率（按粉丝）', f.erPerFollower + '%')}${kpi('平均收藏/帖', f.avg.bookmarks)}
    ${kpi('带图/视频帖热度', st.format.withMediaAvg, '纯文字 ' + st.format.textOnlyAvg)}</div></div>`;
  // topics
  const maxS = Math.max(1, ...an.topics.map(t => t.avgScore));
  h += `<div class="panel"><b>🔥 主要方向（按平均热度排序）</b>${an.topics.length ? '' : '<div class="hint">主题聚类失败：' + esc((an.errors || []).join('；')) + '</div>'}
    <table style="width:100%;font-size:13px;margin-top:8px;border-collapse:collapse">${an.topics.map((t, i) => `<tr style="border-top:1px solid var(--line)"><td style="padding:6px 4px;width:28px">${i + 1}</td>
    <td style="padding:6px 4px"><b>${esc(t.name)}</b><div class="hint">${esc(t.desc)}</div><div class="hint">钩子：${esc(t.hook || '')}</div></td>
    <td style="width:220px"><div class="tbar" style="width:${(100 * t.avgScore / maxS).toFixed(0)}%"></div><span class="hint">平均热度 ${t.avgScore} · ${t.count} 帖（${t.share}%）· 均浏览 ${fmtN(t.avgViews)}</span></td></tr>`).join('')}</table></div>`;
  // frequency + heatmap
  const mx = Math.max(1, ...fr.heatmap.flat().map(c => c.avg));
  h += `<div class="panel"><b>⏰ 互动频率与最佳时段（北京时间）</b><div class="hint" style="margin:6px 0">每天约 ${fr.perDay} 条 · 每周约 ${fr.perWeek} 条 · 统计 ${st.spanDays} 天。
    最佳时段：${fr.bestSlots.slice(0, 5).map(s => `<b>${esc(s.label)}</b>（均热度 ${s.avgScore}，${s.posts} 帖）`).join('、') || '样本不足'}；最佳小时：${fr.bestHours.map(x => x.hour + '点').join('、')}</div>
    <div style="overflow-x:auto"><table class="heat"><tr><th></th>${[...Array(24).keys()].map(x => `<th>${x}</th>`).join('')}</tr>
    ${fr.heatmap.map((row, di) => `<tr><th>${W[di]}</th>${row.map((c, hi) => `<td title="${W[di]} ${hi}点：${c.n} 帖，平均热度 ${c.avg}" style="background:${c.n ? `rgba(79,140,255,${(0.15 + 0.85 * c.avg / mx).toFixed(2)})` : 'transparent'};color:${c.n ? '#fff' : 'var(--muted)'}">${c.n || ''}</td>`).join('')}</tr>`).join('')}</table></div>
    <div class="hint">格子数字 = 发帖数，颜色越深 = 平均热度越高（热度 = 赞 + 2×转发 + 1.5×回复 + 2×引用 + 0.5×收藏）</div></div>`;
  // monetization
  const mo = an.monetization;
  h += `<div class="panel"><b>💰 变现信号（代码检测）</b><div class="hint" style="margin:6px 0">主页链接：${esc(mo.bioLink || '无')} · 带外链的帖子 ${mo.postsWithLinks} 条 · 常见外链域名：${mo.topDomains.map(x => esc(x[0]) + '×' + x[1]).join('、') || '无'}</div>
    ${mo.signals.map(s => `<span class="pill">${esc(s.type)} ×${s.count}</span>`).join('') || '<span class="hint">未检测到明显变现信号</span>'}</div>`;
  if (an.forMe) h += `<div class="panel"><b>🎯 对你的账号${esc(acctLabel(an.forMe.account || window.ACCOUNT, ' '))}：发帖时间建议</b><div style="margin-top:6px">${esc(an.forMe.tip)}</div><div class="hint">「该学什么」见下方报告最后两节；发帖队列会自动排到这些时段。</div></div>`;
  h += `<div class="panel"><b>🧠 分析报告（Claude 主编）</b><div class="md" style="margin-top:6px">${an.report ? md(an.report) : '<span class="hint">报告生成失败：' + esc((an.errors || []).join('；')) + '</span>'}</div></div>`;
  // knowledge base
  const kb = d.kb || { items: [] };
  h += `<div class="panel"><div style="display:flex;justify-content:space-between;align-items:center"><b>📚 爆款知识库（热度前 ${kb.topPercent || 20}% · ${kb.items.length} 条，文字和图片已存到当前账号的本地目录 bench/${esc(u.username.toLowerCase())}/kb/）</b>
    <button class="copy" id="bKbRe">重新下载</button></div>
    <div class="row"><input id="bKbQ" placeholder="搜索关键词" style="flex:1"><select id="bKbT"><option value="">全部主题</option>${[...new Set(kb.items.map(x => x.topic).filter(Boolean))].map(t => `<option>${esc(t)}</option>`).join('')}</select></div>
    <div id="bKbList" style="margin-top:10px"></div></div><div id="bImit"></div>`;
  $('#bOut').innerHTML = h;
  $('#bReAn').onclick = () => runAnalyze(u.username);
  $('#bKbRe').onclick = async () => { prog('重新下载知识库…'); try { await api(`/api/bench/${u.username}/kb`, { method: 'POST', body: '{}' }); prog('知识库已更新', false); openBench(u.username); } catch (e) { prog('失败：' + e.message, false); } };
  const renderKb = () => {
    const q = $('#bKbQ').value.trim().toLowerCase(), tp = $('#bKbT').value;
    const items = kb.items.filter(x => (!tp || x.topic === tp) && (!q || x.text.toLowerCase().includes(q)));
    $('#bKbList').innerHTML = items.map(x => `<div class="kbitem"><div class="hint">#${x.rank} · ${esc(x.topic || '未分类')} · ${esc(x.local_time)} · 热度 ${x.score} · ❤${x.metrics.likes} 🔁${x.metrics.reposts} 💬${x.metrics.replies} 👁${fmtN(x.metrics.views)} · <a href="${esc(x.url)}" target="_blank">原帖</a></div>
      <div style="white-space:pre-wrap;margin-top:6px;font-size:14px">${esc(x.text)}</div>
      <div>${x.media.filter(m => m.saved).map(m => `<a href="/api/bench/${esc(u.username)}/media/${esc(m.file)}" target="_blank"><img src="/api/bench/${esc(u.username)}/media/${esc(m.file)}" loading="lazy" title="${m.type === 'video' ? '视频封面' : '图片'}"></a>`).join('')}</div>
      <div class="row"><button class="run" data-imit="${esc(x.id)}" style="padding:6px 12px;font-size:13px">让 5 个角色仿写这条${window.ACCOUNT && !isDefaultAcct(window.ACCOUNT) ? ' → @' + esc(window.ACCOUNT) : ''}</button></div></div>`).join('') || '<div class="hint">没有匹配的帖子</div>';
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
const QS = { pending: '待发', approved: '已审核·待到点', posted: '已发布', failed: '失败', manual: '需手动发', canceled: '已取消' };
async function loadQueue() { try { renderQueue(await api('/api/queue')); } catch (e) { $('#bQueue').textContent = '读取队列失败：' + e.message; } }
function renderQueue(q) {
  $('#bQCfg').innerHTML = q.postKeysConfigured ? `✅ 已配置发帖密钥：到点后「全自动发」直接发；「审核后发」要你先点「审核通过」。调度器每 ${q.schedulerIntervalSec}s 检查一次（工具要保持运行）。`
    : '⚠️ <b>未配置发帖密钥</b>（.env.local 里的 X_API_KEY / X_API_SECRET / X_ACCESS_TOKEN / X_ACCESS_SECRET）。到点后会标成「需手动发」，点「手动发」用 x.com 打开发帖框。';
  $('#bQueue').innerHTML = q.items.map(x => `<div class="qitem" data-id="${x.id}"><div class="hint"><span class="pill ${x.status}">${QS[x.status] || x.status}</span><span class="pill">${x.mode === 'auto' ? '全自动发' : '审核后发'}</span>
    ${fmtT(x.scheduled_at)}（北京时间）· 加权 ${x.weighted}/280 ${x.source && x.source.username ? '· 仿写自 @' + esc(x.source.username) + (x.source.agent ? ' · ' + esc(x.source.agent) : '') : ''}
    ${x.tweetId ? ` · <a href="https://x.com/i/status/${esc(x.tweetId)}" target="_blank">查看已发帖子</a>` : ''}${x.error ? ` · <span style="color:var(--err)">${esc(x.error)}</span>` : ''}</div>
    <div style="white-space:pre-wrap;margin:6px 0;font-size:14px">${esc(x.text)}</div>
    ${x.status === 'posted' || x.status === 'canceled' ? `<div class="row" style="margin-top:0"><button class="copy" data-a="delete">删除</button></div>` : `<div class="row" style="margin-top:0">
      ${x.mode === 'review' && x.status !== 'approved' ? '<button class="copy" data-a="approve">审核通过</button>' : ''}
      <button class="copy" data-a="toggle">改成${x.mode === 'auto' ? '审核后发' : '全自动发'}</button>
      ${q.postKeysConfigured ? '<button class="copy" data-a="post_now">立即发</button>' : `<a class="copy" target="_blank" href="${esc(x.intent)}">手动发</a><button class="copy" data-a="mark_posted">我已手动发好</button>`}
      <button class="copy" data-a="edit">编辑</button><button class="copy" data-a="cancel">取消</button></div>`}</div>`).join('') || '<div class="hint">队列是空的。在知识库里点「让 5 个角色仿写」，再把满意的稿子加入队列。</div>';
  $('#bQueue').querySelectorAll('[data-a]').forEach(b => b.onclick = async () => {
    const id = b.closest('.qitem').dataset.id, x = q.items.find(i => i.id === id); let body = { action: b.dataset.a };
    if (b.dataset.a === 'toggle') body = { action: 'update', mode: x.mode === 'auto' ? 'review' : 'auto' };
    if (b.dataset.a === 'edit') { const t = prompt('编辑帖子内容', x.text); if (t == null) return; body = { action: 'update', text: t }; }
    if (b.dataset.a === 'post_now' && !confirm('现在就用' + acctLabel(window.ACCOUNT, ' ', '你的账号') + ' 发出这条帖子？')) return;
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
