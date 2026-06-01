/**
 * static/copilot.js — Outreach Copilot Floating Panel
 * Include on any page: <script src="/static/copilot.js" data-page="campaign_status" data-page-id="123"></script>
 */
(function() {
  const script = document.currentScript;
  const PAGE_TYPE = script.getAttribute('data-page') || '';
  const PAGE_ID = script.getAttribute('data-page-id') || '0';

  // Inject CSS
  const style = document.createElement('style');
  style.textContent = `
    #copilot-fab{position:fixed;bottom:24px;right:24px;width:52px;height:52px;border-radius:16px;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 4px 20px rgba(99,102,241,0.4);z-index:9998;transition:all 0.2s;font-size:20px;}
    #copilot-fab:hover{transform:scale(1.08);box-shadow:0 6px 28px rgba(99,102,241,0.5);}
    #copilot-fab.has-response{animation:copilot-bounce 0.4s ease;}
    @keyframes copilot-bounce{0%,100%{transform:scale(1);}50%{transform:scale(1.15);}}
    #copilot-panel{position:fixed;bottom:88px;right:24px;width:380px;max-height:520px;background:#fff;border:1px solid #e2e8f0;border-radius:16px;box-shadow:0 8px 40px rgba(15,23,42,0.15);z-index:9999;display:none;flex-direction:column;overflow:hidden;font-family:'Inter',system-ui,sans-serif;}
    #copilot-panel.open{display:flex;}
    .cp-header{display:flex;align-items:center;gap:10px;padding:14px 16px;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;flex-shrink:0;}
    .cp-header-icon{width:28px;height:28px;background:rgba(255,255,255,0.2);border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:13px;}
    .cp-header-title{font-size:13px;font-weight:700;flex:1;}
    .cp-header-close{cursor:pointer;opacity:0.7;font-size:14px;padding:4px;}.cp-header-close:hover{opacity:1;}
    .cp-body{flex:1;overflow-y:auto;padding:14px;min-height:200px;max-height:360px;}
    .cp-body::-webkit-scrollbar{width:4px;}.cp-body::-webkit-scrollbar-thumb{background:#e2e8f0;border-radius:2px;}
    .cp-msg{margin-bottom:12px;animation:cp-fade 0.3s ease;}
    @keyframes cp-fade{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:translateY(0);}}
    .cp-msg-user{text-align:right;}
    .cp-msg-user .cp-bubble{background:#6366f1;color:#fff;display:inline-block;padding:8px 12px;border-radius:12px 12px 4px 12px;font-size:12.5px;max-width:85%;text-align:left;}
    .cp-msg-ai .cp-bubble{background:#f1f5f9;color:#111827;display:inline-block;padding:10px 14px;border-radius:12px 12px 12px 4px;font-size:12.5px;max-width:90%;line-height:1.5;}
    .cp-actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;}
    .cp-action-btn{display:inline-flex;align-items:center;gap:5px;padding:6px 12px;border-radius:8px;font-size:11.5px;font-weight:600;border:1px solid #e2e8f0;background:#fff;color:#374151;cursor:pointer;transition:all 0.15s;}
    .cp-action-btn:hover{background:#eef2ff;border-color:#6366f1;color:#4338ca;}
    .cp-action-btn.confirm{border-color:#fecaca;color:#dc2626;}.cp-action-btn.confirm:hover{background:#fef2f2;}
    .cp-action-btn.safe{border-color:#bbf7d0;color:#15803d;}.cp-action-btn.safe:hover{background:#f0fdf4;}
    .cp-typing{display:flex;align-items:center;gap:4px;padding:8px 14px;}
    .cp-typing span{width:6px;height:6px;background:#9ca3af;border-radius:50%;animation:cp-dot 1.2s infinite;}
    .cp-typing span:nth-child(2){animation-delay:0.2s;}.cp-typing span:nth-child(3){animation-delay:0.4s;}
    @keyframes cp-dot{0%,60%,100%{transform:translateY(0);}30%{transform:translateY(-4px);}}
    .cp-input-wrap{display:flex;align-items:center;gap:8px;padding:12px 14px;border-top:1px solid #f1f5f9;flex-shrink:0;}
    .cp-input{flex:1;border:1px solid #e2e8f0;border-radius:10px;padding:9px 12px;font-size:12.5px;outline:none;resize:none;font-family:inherit;max-height:60px;}
    .cp-input:focus{border-color:#6366f1;box-shadow:0 0 0 3px rgba(99,102,241,0.1);}
    .cp-send{width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:13px;transition:opacity 0.15s;}
    .cp-send:hover{opacity:0.85;}.cp-send:disabled{opacity:0.4;cursor:not-allowed;}
    .cp-empty{text-align:center;padding:30px 20px;color:#9ca3af;}
    .cp-empty i{font-size:24px;margin-bottom:10px;display:block;color:#c7d2fe;}
    .cp-empty p{font-size:12px;margin:0;}
    .cp-quick{display:flex;flex-wrap:wrap;gap:6px;padding:0 14px 12px;}
    .cp-quick-btn{font-size:11px;padding:5px 10px;border-radius:99px;border:1px solid #e2e8f0;background:#f8fafc;color:#475569;cursor:pointer;transition:all 0.15s;white-space:nowrap;}
    .cp-quick-btn:hover{background:#eef2ff;border-color:#6366f1;color:#4338ca;}
  `;
  document.head.appendChild(style);

  // Quick suggestions per page
  const QUICK = {
    campaign_status: ['Why are emails failing?', 'Should I pause?', 'SMTP health check', 'Retry failed emails'],
    inbox_thread: ['Draft a reply', 'What does this contact want?', 'Suggest next steps', 'Summarize thread'],
    contacts: ['How many are enriched?', 'Enrich all contacts', 'Show invalid emails', 'Lead score breakdown']
  };

  // Build DOM
  const fab = document.createElement('div');
  fab.id = 'copilot-fab';
  fab.innerHTML = '<i class="fas fa-sparkles"></i>';
  fab.title = 'Outreach Copilot';
  document.body.appendChild(fab);

  const panel = document.createElement('div');
  panel.id = 'copilot-panel';
  panel.innerHTML = `
    <div class="cp-header">
      <div class="cp-header-icon"><i class="fas fa-sparkles"></i></div>
      <div class="cp-header-title">Outreach Copilot</div>
      <div class="cp-header-close" id="cp-close"><i class="fas fa-times"></i></div>
    </div>
    <div class="cp-body" id="cp-body">
      <div class="cp-empty"><i class="fas fa-robot"></i><p>Ask me anything about this page.<br>I can diagnose issues, draft replies, and suggest actions.</p></div>
    </div>
    <div class="cp-quick" id="cp-quick"></div>
    <div class="cp-input-wrap">
      <textarea class="cp-input" id="cp-input" placeholder="Ask Copilot..." rows="1"></textarea>
      <button class="cp-send" id="cp-send"><i class="fas fa-arrow-up"></i></button>
    </div>
  `;
  document.body.appendChild(panel);

  // Quick suggestions
  const quickWrap = document.getElementById('cp-quick');
  (QUICK[PAGE_TYPE] || []).forEach(q => {
    const btn = document.createElement('button');
    btn.className = 'cp-quick-btn';
    btn.textContent = q;
    btn.onclick = () => sendMessage(q);
    quickWrap.appendChild(btn);
  });

  // Toggle
  let isOpen = false;
  fab.onclick = () => { isOpen = !isOpen; panel.classList.toggle('open', isOpen); if(isOpen) document.getElementById('cp-input').focus(); };
  document.getElementById('cp-close').onclick = () => { isOpen = false; panel.classList.remove('open'); };

  // Send
  const input = document.getElementById('cp-input');
  const sendBtn = document.getElementById('cp-send');
  sendBtn.onclick = () => sendMessage(input.value);
  input.onkeydown = (e) => { if(e.key==='Enter' && !e.shiftKey){e.preventDefault(); sendMessage(input.value);} };

  let sending = false;

  async function sendMessage(msg) {
    msg = msg.trim();
    if (!msg || sending) return;
    sending = true;
    sendBtn.disabled = true;
    input.value = '';

    const body = document.getElementById('cp-body');
    // Clear empty state
    const empty = body.querySelector('.cp-empty');
    if (empty) empty.remove();
    // Hide quick suggestions after first message
    quickWrap.style.display = 'none';

    // User bubble
    body.innerHTML += `<div class="cp-msg cp-msg-user"><div class="cp-bubble">${esc(msg)}</div></div>`;
    // Typing indicator
    body.innerHTML += `<div class="cp-msg cp-msg-ai" id="cp-typing"><div class="cp-typing"><span></span><span></span><span></span></div></div>`;
    body.scrollTop = body.scrollHeight;

    try {
      const r = await fetch('/api/copilot/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: msg, page_type: PAGE_TYPE, page_id: PAGE_ID})
      });
      const d = await r.json();
      // Remove typing
      const typing = document.getElementById('cp-typing');
      if (typing) typing.remove();

      if (d.success) {
        let html = `<div class="cp-msg cp-msg-ai"><div class="cp-bubble">${formatMsg(d.message)}</div>`;
        if (d.actions && d.actions.length) {
          html += '<div class="cp-actions">';
          d.actions.forEach((a) => {
            const cls = isConfirmAction(a.type) ? 'confirm' : 'safe';
            html += `<button class="cp-action-btn ${cls}" data-action='${esc(JSON.stringify(a))}'>${esc(a.label)}</button>`;
          });
          html += '</div>';
        }
        html += '</div>';
        body.innerHTML += html;
        fab.classList.add('has-response');
        setTimeout(() => fab.classList.remove('has-response'), 500);
      } else {
        body.innerHTML += `<div class="cp-msg cp-msg-ai"><div class="cp-bubble" style="color:#dc2626">${esc(d.error || 'Something went wrong')}</div></div>`;
      }
    } catch(e) {
      const typing = document.getElementById('cp-typing');
      if (typing) typing.remove();
      body.innerHTML += `<div class="cp-msg cp-msg-ai"><div class="cp-bubble" style="color:#dc2626">Network error. Try again.</div></div>`;
    }

    body.scrollTop = body.scrollHeight;
    sending = false;
    sendBtn.disabled = false;
  }

  // Action execution — delegate click via event delegation on body
  document.body.addEventListener('click', async function(e) {
    const btnEl = e.target.closest('.cp-action-btn');
    if (!btnEl) return;
    let actionData;
    try { actionData = JSON.parse(btnEl.getAttribute('data-action')); } catch(ex) { return; }
    if (!actionData) return;

    // Confirm dangerous actions
    if (isConfirmAction(actionData.type)) {
      if (!confirm(`Execute: ${actionData.label}?`)) return;
    }

    btnEl.disabled = true;
    btnEl.textContent = '...';

    // ALL actions route through /api/copilot/action for server-side workspace checks
    try {
      const r = await fetch('/api/copilot/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action_type: actionData.type, params: actionData.params || {}, page_type: PAGE_TYPE, page_id: PAGE_ID})
      });
      const d = await r.json();
      if (d.success) {
        // Handle draft_reply result — populate textarea if present
        if (actionData.type === 'draft_reply' && d.draft) {
          const ta = document.getElementById('replyDraft');
          if (ta) ta.value = d.draft;
        }
        btnEl.textContent = d.message ? '✓ ' + d.message.substring(0, 30) : '✓ Done';
        btnEl.style.borderColor = '#bbf7d0';
        btnEl.style.color = '#15803d';
      } else {
        btnEl.textContent = '✗ ' + (d.error || 'Failed');
        btnEl.style.borderColor = '#fecaca';
        btnEl.style.color = '#dc2626';
      }
    } catch(ex) {
      btnEl.textContent = '✗ Network error';
      btnEl.style.borderColor = '#fecaca';
      btnEl.style.color = '#dc2626';
    }
  });

  function isConfirmAction(type) {
    return ['retry_failed','pause_campaign','resume_campaign','cancel_campaign','send_reply','bulk_enrich'].includes(type);
  }

  function formatMsg(text) {
    // Basic formatting: newlines to <br>, **bold**
    return esc(text).replace(/\n/g, '<br>').replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
  }

  function esc(s) { const d=document.createElement('div'); d.textContent=s||''; return d.innerHTML; }
})();
