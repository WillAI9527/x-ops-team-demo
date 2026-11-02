// v1.5/v1.6 多账号：开始页（你的 X 账号）+ 右上角账号切换。所有 @LucaAiAi 文案替换成当前账号。
(function () {
  const css = `#acctBar{position:fixed;top:10px;right:14px;z-index:50;display:flex;gap:6px;align-items:center;background:var(--panel,#151a23);border:1px solid var(--line,#2a3140);border-radius:10px;padding:6px 8px;font-size:13px}
  #acctBar img{width:22px;height:22px;border-radius:50%} #acctBar select{max-width:180px}
  #startPage{position:fixed;inset:0;z-index:100;background:rgba(8,10,15,.92);display:flex;align-items:center;justify-content:center}
  #startPage .box{background:var(--panel,#151a23);border:1px solid var(--line,#2a3140);border-radius:14px;padding:24px;width:min(520px,92vw)}
  #startPage input{width:100%;font-size:16px;padding:10px;margin:10px 0} #startPage .hint{font-size:13px}`;
  document.head.insertAdjacentHTML('beforeend', `<style>${css}</style>`);
  const H = () => { const h = { 'Content-Type': 'application/json' }; const t = localStorage.getItem('luca_token'); if (t) h['Authorization'] = 'Bearer ' + t; return h; };
  const E = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  async function j(path, opt = {}) { const r = await fetch(path, { headers: H(), ...opt }); const x = await r.json().catch(() => ({})); if (!r.ok) throw new Error(x.error || 'HTTP ' + r.status); return x; }
  // v1.6 bug fix: relabel used to walk the WHOLE body, including the account <select> in the top-right,
  // so "@LucaAiAi" options were renamed to the current handle (two identical names, wrong label).
  const SKIP = n => { const e = n.nodeType === 1 ? n : n.parentElement; return !!(e && e.closest && e.closest('#acctBar,#startPage,.noRelabel')); };
  function relabel(h) {
    if (!h || h.toLowerCase() === 'lucaaiai') return;
    const walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, { acceptNode: n => SKIP(n) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT });
    let n; while ((n = walk.nextNode())) if (n.nodeValue.includes('LucaAiAi')) n.nodeValue = n.nodeValue.replace(/@?LucaAiAi/g, '@' + h);
    document.querySelectorAll('[placeholder]').forEach(e => { if (e.placeholder.includes('LucaAiAi')) e.placeholder = e.placeholder.replace(/@LucaAiAi/g, '@' + h).replace(/AI 科普账号/g, '账号'); });
    document.title = document.title.replace(/@LucaAiAi/g, '@' + h);
    new MutationObserver(ms => ms.forEach(m => m.addedNodes.forEach(nd => {
      if (SKIP(nd)) return;
      const w = document.createTreeWalker(nd, NodeFilter.SHOW_TEXT); let t; if (nd.nodeType === 3) { if (nd.nodeValue.includes('@LucaAiAi')) nd.nodeValue = nd.nodeValue.replace(/@LucaAiAi/g, '@' + h); return; }
      while ((t = w.nextNode())) if (t.nodeValue.includes('@LucaAiAi')) t.nodeValue = t.nodeValue.replace(/@LucaAiAi/g, '@' + h);
    }))).observe(document.body, { childList: true, subtree: true });
  }
  function startPage(cancelable) {
    const d = document.createElement('div'); d.id = 'startPage';
    d.innerHTML = `<div class="box"><h2 style="margin:0 0 6px">你的 X 账号</h2>
      <div class="hint">填你自己的 X 用户名。工具会免密读取它的公开资料和最近帖子，判断账号定位；之后 5 个角色、审查狼、策略、辩论、对标分析都按这个账号来做，数据按账号分开存。</div>
      <input id="spUser" placeholder="例如 LucaAiAi 或 https://x.com/LucaAiAi">
      <div class="row" style="display:flex;gap:8px;align-items:center"><button class="run" id="spGo">开始</button>${cancelable ? '<button class="copy" id="spCancel">取消</button>' : ''}<span class="hint" id="spMsg"></span></div></div>`;
    document.body.appendChild(d);
    const go = async () => {
      const u = $('#spUser').value.trim().replace(/^@/, '').replace(/^https?:\/\/(x|twitter)\.com\//, '').split(/[/?]/)[0];
      if (!/^[A-Za-z0-9_]{1,15}$/.test(u)) { $('#spMsg').textContent = '用户名不合法'; return; }
      $('#spGo').disabled = true; $('#spMsg').innerHTML = '<span class="spin"></span>正在读取 @' + E(u) + ' 的公开资料和帖子，并分析定位（约 20–60 秒）…';
      try { const r = await j('/api/accounts', { method: 'POST', body: JSON.stringify({ username: u }) });
        if (r.warning) alert('已创建账号，但没读到帖子：' + r.warning + '\n定位先按「待确认」处理，可稍后在右上角「重新识别」。');
        location.hash = ''; location.reload();
      } catch (e) { $('#spMsg').textContent = '失败：' + e.message; $('#spGo').disabled = false; }
    };
    $('#spGo').onclick = go; $('#spUser').onkeydown = e => { if (e.key === 'Enter') go(); };
    if (cancelable) $('#spCancel').onclick = () => d.remove();
    $('#spUser').focus();
  }
  async function init() {
    let a; try { a = await j('/api/accounts', { cache: 'no-store' }); } catch (e) { return; }
    window.ACCOUNT = a.active;
    // v1.6: if another tab switched/added an account, reload so this page never shows a stale name.
    const recheck = async () => { try { const x = await j('/api/accounts', { cache: 'no-store' }); if ((x.active || '').toLowerCase() !== (window.ACCOUNT || '').toLowerCase()) { location.hash = ''; location.reload(); } } catch (e) {} };
    window.addEventListener('focus', recheck); document.addEventListener('visibilitychange', () => { if (!document.hidden) recheck(); });
    if (!a.active) return startPage(false);
    const p = a.profile || {};
    const bar = document.createElement('div'); bar.id = 'acctBar';
    bar.innerHTML = `${p.avatar ? `<img src="${E(p.avatar)}">` : ''}<select id="acctSel" class="noRelabel" title="${E(p.positioning || '')}">${a.accounts.map(x => `<option value="${E(x.handle)}" ${(x.handle || '').toLowerCase() === (a.active || '').toLowerCase() ? 'selected' : ''}>@${E(x.handle)}${x.short ? ' · ' + E(x.short) : ''}</option>`).join('')}<option value="__add">＋ 添加账号…</option><option value="__re">↻ 重新识别当前账号定位</option></select>`;
    document.body.appendChild(bar);
    $('#acctSel').onchange = async e => {
      const v = e.target.value;
      if (v === '__add') { e.target.value = a.active; return startPage(true); }
      if (v === '__re') { e.target.value = a.active; bar.insertAdjacentHTML('beforeend', '<span class="spin" id="acctSpin"></span>');
        try { await j('/api/accounts', { method: 'POST', body: JSON.stringify({ username: a.active, reinfer: true }) }); location.reload(); } catch (err) { alert(err.message); $('#acctSpin')?.remove(); } return; }
      await j('/api/accounts/switch', { method: 'POST', body: JSON.stringify({ handle: v }) }); location.hash = ''; location.reload();
    };
    const hdr = document.querySelector('header p');
    if (hdr) hdr.insertAdjacentHTML('beforebegin', `<p class="hint noRelabel" id="acctLine" style="margin:4px 0">当前账号：<b>@${E(a.active)}</b> · 定位：${E(p.positioning || '待确认')}${p.followers != null ? ' · 粉丝 ' + E(p.followers) : ''}</p>`);
    relabel(a.active);
  }
  init();
})();
