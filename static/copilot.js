/**
 * static/copilot.js — OutreachOS AI Copilot v2
 * Phase 5: Full UX redesign with alerts, agents, markdown, shortcuts
 * Include: <script src="/static/copilot.js" data-page="dashboard" data-page-id="0"></script>
 */
(function() {
  const script = document.currentScript;
  const PAGE_TYPE = script.getAttribute('data-page') || '';
  const PAGE_ID = script.getAttribute('data-page-id') || '0';
  const SESSION_KEY = 'copilot_history_' + PAGE_TYPE;

  // ── STYLES ──
  const style = document.createElement('style');
  style.textContent = `
    #copilot-fab{position:fixed;bottom:24px;right:24px;width:54px;height:54px;border-radius:16px;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:0 4px 24px rgba(99,102,241,0.4);z-index:9998;transition:all 0.2s;font-size:20px;}
    #copilot-fab:hover{transform:scale(1.08);box-shadow:0 6px 32px rgba(99,102,241,0.5);}
    #copilot-fab .fab-badge{position:absolute;top:-4px;right:-4px;min-width:18px;height:18px;border-radius:99px;background:#ef4444;color:#fff;font-size:10px;font-weight:700;display:flex;align-items:center;justify-content:center;padding:0 4px;border:2px solid #fff;display:none;}
    #copilot-fab .fab-badge.show{display:flex;}

    #copilot-panel{position:fixed;bottom:88px;right:24px;width:400px;max-height:600px;background:#fff;border:1px solid #e2e8f0;border-radius:18px;box-shadow:0 12px 48px rgba(15,23,42,0.15);z-index:9999;display:none;flex-direction:column;overflow:hidden;font-family:'Inter',system-ui,sans-serif;}
    #copilot-panel.open{display:flex;}

    .cp-header{display:flex;align-items:center;gap:10px;padding:14px 18px;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;flex-shrink:0;}
    .cp-header-icon{width:30px;height:30px;background:rgba(255,255,255,0.2);border-radius:9px;display:flex;align-items:center;justify-content:center;font-size:14px;}
    .cp-header-title{font-size:13.5px;font-weight:700;flex:1;}
    .cp-header-subtitle{font-size:10px;opacity:0.8;font-weight:400;}
    .cp-header-actions{display:flex;gap:6px;}
    .cp-header-btn{cursor:pointer;opacity:0.7;font-size:13px;padding:4px 6px;border-radius:6px;transition:all 0.15s;}.cp-header-btn:hover{opacity:1;background:rgba(255,255,255,0.15);}

    .cp-tabs{display:flex;border-bottom:1px solid #f1f5f9;flex-shrink:0;}
    .cp-tab{flex:1;text-align:center;padding:9px 0;font-size:11.5px;font-weight:600;color:#9ca3af;cursor:pointer;transition:all 0.15s;border-bottom:2px solid transparent;}
    .cp-tab:hover{color:#6366f1;}
    .cp-tab.active{color:#6366f1;border-bottom-color:#6366f1;}
    .cp-tab .tab-dot{display:inline-block;width:6px;height:6px;border-radius:50%;margin-left:4px;}

    .cp-view{flex:1;overflow-y:auto;display:none;flex-direction:column;}
    .cp-view.active{display:flex;}

    .cp-body{flex:1;overflow-y:auto;padding:14px 16px;min-height:180px;}
    .cp-body::-webkit-scrollbar{width:4px;}.cp-body::-webkit-scrollbar-thumb{background:#e2e8f0;border-radius:2px;}

    .cp-msg{margin-bottom:12px;animation:cp-fade 0.25s ease;}
    @keyframes cp-fade{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:translateY(0);}}
    .cp-msg-user{text-align:right;}
    .cp-msg-user .cp-bubble{background:#6366f1;color:#fff;display:inline-block;padding:9px 13px;border-radius:14px 14px 4px 14px;font-size:12.5px;max-width:85%;text-align:left;line-height:1.5;}
    .cp-msg-ai .cp-bubble{background:#f8fafc;color:#111827;display:inline-block;padding:11px 14px;border-radius:14px 14px 14px 4px;font-size:12.5px;max-width:92%;line-height:1.6;border:1px solid #f1f5f9;}
    .cp-msg-ai .cp-bubble strong{color:#4338ca;}
    .cp-msg-ai .cp-bubble code{background:#eef2ff;color:#6366f1;padding:1px 5px;border-radius:4px;font-size:11.5px;}
    .cp-msg-ai .cp-bubble ul,.cp-msg-ai .cp-bubble ol{margin:6px 0;padding-left:18px;}
    .cp-msg-ai .cp-bubble li{margin-bottom:3px;}

    .cp-actions{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px;}
    .cp-action-btn{display:inline-flex;align-items:center;gap:5px;padding:6px 12px;border-radius:8px;font-size:11px;font-weight:600;border:1px solid #e2e8f0;background:#fff;color:#374151;cursor:pointer;transition:all 0.15s;}
    .cp-action-btn:hover{background:#eef2ff;border-color:#6366f1;color:#4338ca;}
    .cp-action-btn.confirm{border-color:#fecaca;color:#dc2626;}.cp-action-btn.confirm:hover{background:#fef2f2;}
    .cp-action-btn.safe{border-color:#bbf7d0;color:#15803d;}.cp-action-btn.safe:hover{background:#f0fdf4;}

    .cp-typing{display:flex;align-items:center;gap:4px;padding:8px 14px;}
    .cp-typing span{width:6px;height:6px;background:#a5b4fc;border-radius:50%;animation:cp-dot 1.2s infinite;}
    .cp-typing span:nth-child(2){animation-delay:0.2s;}.cp-typing span:nth-child(3){animation-delay:0.4s;}
    @keyframes cp-dot{0%,60%,100%{transform:translateY(0);}30%{transform:translateY(-4px);}}

    .cp-input-wrap{display:flex;align-items:center;gap:8px;padding:12px 14px;border-top:1px solid #f1f5f9;flex-shrink:0;background:#fff;}
    .cp-input{flex:1;border:1px solid #e5e7eb;border-radius:10px;padding:9px 12px;font-size:12.5px;outline:none;resize:none;font-family:inherit;max-height:60px;transition:border-color 0.15s,box-shadow 0.15s;}
    .cp-input:focus{border-color:#6366f1;box-shadow:0 0 0 3px rgba(99,102,241,0.08);}
    .cp-send{width:34px;height:34px;border-radius:10px;background:linear-gradient(135deg,#6366f1,#8b5cf6);color:#fff;border:none;cursor:pointer;display:flex;align-items:center;justify-content:center;font-size:13px;transition:opacity 0.15s;}
    .cp-send:hover{opacity:0.85;}.cp-send:disabled{opacity:0.4;cursor:not-allowed;}

    .cp-empty{text-align:center;padding:28px 20px;color:#9ca3af;}
    .cp-empty i{font-size:28px;margin-bottom:10px;display:block;color:#c7d2fe;}
    .cp-empty p{font-size:12px;margin:4px 0 0;line-height:1.5;}

    .cp-quick{display:flex;flex-wrap:wrap;gap:6px;padding:8px 14px 12px;}
    .cp-quick-btn{font-size:11px;padding:5px 10px;border-radius:99px;border:1px solid #e5e7eb;background:#f8fafc;color:#475569;cursor:pointer;transition:all 0.15s;white-space:nowrap;}
    .cp-quick-btn:hover{background:#eef2ff;border-color:#6366f1;color:#4338ca;}

    /* Alerts view */
    .cp-alert-card{padding:10px 14px;margin:0 14px 8px;border-radius:10px;border:1px solid #fecaca;background:#fef7f7;display:flex;align-items:flex-start;gap:8px;animation:cp-fade 0.3s;}
    .cp-alert-card.warn{border-color:#fde68a;background:#fffdf5;}
    .cp-alert-card.info{border-color:#bfdbfe;background:#f0f7ff;}
    .cp-alert-card .alert-icon{font-size:13px;margin-top:1px;flex-shrink:0;}
    .cp-alert-card .alert-body{flex:1;min-width:0;}
    .cp-alert-card .alert-title{font-size:12px;font-weight:600;color:#111827;margin-bottom:2px;}
    .cp-alert-card .alert-desc{font-size:11px;color:#6b7280;line-height:1.4;}
    .cp-alert-card .alert-dismiss{cursor:pointer;font-size:11px;color:#9ca3af;padding:2px;opacity:0.6;}.cp-alert-card .alert-dismiss:hover{opacity:1;}
    .cp-alerts-empty{text-align:center;padding:30px 20px;color:#9ca3af;font-size:12px;}

    /* Agents view */
    .cp-agent-card{padding:10px 14px;margin:0 14px 8px;border-radius:10px;border:1px solid #e5e7eb;background:#f8fafc;display:flex;align-items:center;gap:10px;cursor:pointer;transition:all 0.15s;}
    .cp-agent-card:hover{border-color:#6366f1;background:#eef2ff;}
    .cp-agent-card .agent-icon{width:32px;height:32px;border-radius:9px;display:flex;align-items:center;justify-content:center;font-size:14px;}
    .cp-agent-card .agent-info{flex:1;}
    .cp-agent-card .agent-name{font-size:12px;font-weight:600;color:#111827;}
    .cp-agent-card .agent-desc{font-size:10.5px;color:#9ca3af;}
    .cp-agent-card .agent-status{width:7px;height:7px;border-radius:50%;background:#10b981;}

    /* Shortcut hint */
    .cp-shortcut{position:fixed;bottom:82px;right:30px;font-size:10px;color:#9ca3af;z-index:9997;pointer-events:none;opacity:0;transition:opacity 0.3s;}
    .cp-shortcut.show{opacity:1;}
  `;
  document.head.appendChild(style);

  // ── QUICK SUGGESTIONS ──
  const QUICK = {
    dashboard: ['📊 Performance summary', '🚨 Any issues?', '🎯 What should I do?', '📈 Best performing campaign'],
    campaign_status: ['❓ Why are emails failing?', '⏸️ Should I pause?', '🔧 SMTP health check', '🔄 Retry failed'],
    inbox: ['📬 Prioritize inbox', '✍️ Draft a reply', '📋 Summarize threads', '🏷️ Classify replies'],
    inbox_thread: ['✍️ Draft a reply', '🤔 What do they want?', '➡️ Suggest next steps', '📋 Summarize thread'],
    contacts: ['📊 Enrichment status', '🔍 Enrich all', '❌ Invalid emails', '🎯 Lead scores'],
    deliverability: ['🏥 Full health check', '🔥 Warmup advice', '📉 Bounce analysis', '⚠️ At-risk accounts'],
    campaigns: ['🏆 Best campaign', '📊 Compare campaigns', '💡 Strategy advice', '📉 Diagnose failures'],
    analytics: ['📊 Generate report', '📈 Detect trends', '⏰ Best send time', '🔮 Forecast']
  };

  const AGENTS = [
    {type:'deliverability', name:'Deliverability Agent', desc:'SMTP health, warmup, bounces', icon:'🛡️', color:'#0ea5e9'},
    {type:'campaign', name:'Campaign Agent', desc:'Strategy, analysis, optimization', icon:'📣', color:'#8b5cf6'},
    {type:'inbox', name:'Inbox Agent', desc:'Replies, drafts, prioritization', icon:'📬', color:'#f59e0b'},
    {type:'research', name:'Research Agent', desc:'Lead enrichment, ICP scoring', icon:'🔍', color:'#10b981'},
    {type:'analytics', name:'Analytics Agent', desc:'Reports, trends, forecasting', icon:'📊', color:'#6366f1'}
  ];

  // ── BUILD DOM ──
  const fab = document.createElement('div');
  fab.id = 'copilot-fab';
  fab.innerHTML = '<i class="fas fa-robot"></i><span class="fab-badge" id="fab-badge">0</span>';
  fab.title = 'AI Copilot (Ctrl+K)';
  document.body.appendChild(fab);

  const shortcutHint = document.createElement('div');
  shortcutHint.className = 'cp-shortcut';
  shortcutHint.textContent = 'Ctrl+K';
  document.body.appendChild(shortcutHint);

  const panel = document.createElement('div');
  panel.id = 'copilot-panel';
  panel.innerHTML = `
    <div class="cp-header">
      <div class="cp-header-icon"><i class="fas fa-robot"></i></div>
      <div style="flex:1;">
        <div class="cp-header-title">AI Copilot</div>
        <div class="cp-header-subtitle">OutreachOS Intelligence</div>
      </div>
      <div class="cp-header-actions">
        <div class="cp-header-btn" id="cp-clear" title="Clear chat"><i class="fas fa-trash-alt"></i></div>
        <div class="cp-header-btn" id="cp-close" title="Close"><i class="fas fa-times"></i></div>
      </div>
    </div>
    <div class="cp-tabs">
      <div class="cp-tab active" data-tab="chat"><i class="fas fa-comments"></i> Chat</div>
      <div class="cp-tab" data-tab="alerts"><i class="fas fa-bell"></i> Alerts<span class="tab-dot" id="alert-dot" style="background:#ef4444;display:none;"></span></div>
      <div class="cp-tab" data-tab="agents"><i class="fas fa-users-cog"></i> Agents</div>
    </div>
    <div class="cp-view active" data-view="chat">
      <div class="cp-body" id="cp-body">
        <div class="cp-empty"><i class="fas fa-sparkles"></i><p>Your AI assistant for outreach.<br>Ask anything or pick a suggestion below.</p></div>
      </div>
      <div class="cp-quick" id="cp-quick"></div>
      <div class="cp-input-wrap">
        <textarea class="cp-input" id="cp-input" placeholder="Ask Copilot... (Ctrl+K)" rows="1"></textarea>
        <button class="cp-send" id="cp-send"><i class="fas fa-arrow-up"></i></button>
      </div>
    </div>
    <div class="cp-view" data-view="alerts">
      <div style="padding:12px 0;overflow-y:auto;flex:1;" id="cp-alerts-wrap">
        <div class="cp-alerts-empty" id="cp-alerts-empty"><i class="fas fa-check-circle" style="font-size:20px;color:#10b981;display:block;margin-bottom:8px;"></i>No active alerts</div>
      </div>
    </div>
    <div class="cp-view" data-view="agents">
      <div style="padding:12px 0;overflow-y:auto;flex:1;" id="cp-agents-wrap"></div>
    </div>
  `;
  document.body.appendChild(panel);

  // ── INIT QUICK SUGGESTIONS ──
  const quickWrap = document.getElementById('cp-quick');
  (QUICK[PAGE_TYPE] || QUICK.dashboard).forEach(q => {
    const btn = document.createElement('button');
    btn.className = 'cp-quick-btn';
    btn.textContent = q;
    btn.onclick = () => sendMessage(q.replace(/^[^\s]+\s/, ''));
    quickWrap.appendChild(btn);
  });

  // ── INIT AGENTS ──
  const agentsWrap = document.getElementById('cp-agents-wrap');
  AGENTS.forEach(a => {
    const card = document.createElement('div');
    card.className = 'cp-agent-card';
    card.innerHTML = `
      <div class="agent-icon" style="background:${a.color}20;color:${a.color};">${a.icon}</div>
      <div class="agent-info"><div class="agent-name">${a.name}</div><div class="agent-desc">${a.desc}</div></div>
      <div class="agent-status"></div>
    `;
    card.onclick = () => { switchTab('chat'); sendMessage(`Run ${a.name.toLowerCase()} health check`); };
    agentsWrap.appendChild(card);
  });

  // ── TABS ──
  const tabs = panel.querySelectorAll('.cp-tab');
  const views = panel.querySelectorAll('.cp-view');
  tabs.forEach(tab => {
    tab.onclick = () => switchTab(tab.getAttribute('data-tab'));
  });

  function switchTab(name) {
    tabs.forEach(t => t.classList.toggle('active', t.getAttribute('data-tab') === name));
    views.forEach(v => v.classList.toggle('active', v.getAttribute('data-view') === name));
    if (name === 'alerts') loadAlerts();
  }

  // ── TOGGLE PANEL ──
  let isOpen = false;
  fab.onclick = () => togglePanel();
  document.getElementById('cp-close').onclick = () => togglePanel(false);

  function togglePanel(force) {
    isOpen = force !== undefined ? force : !isOpen;
    panel.classList.toggle('open', isOpen);
    if (isOpen) {
      document.getElementById('cp-input').focus();
      loadAlerts();
    }
  }

  // ── KEYBOARD SHORTCUT ──
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      togglePanel();
    }
    if (e.key === 'Escape' && isOpen) togglePanel(false);
  });

  // Show hint on fab hover
  fab.onmouseenter = () => shortcutHint.classList.add('show');
  fab.onmouseleave = () => shortcutHint.classList.remove('show');

  // ── CLEAR CHAT ──
  document.getElementById('cp-clear').onclick = () => {
    const body = document.getElementById('cp-body');
    body.innerHTML = '<div class="cp-empty"><i class="fas fa-sparkles"></i><p>Chat cleared. Ask me anything.</p></div>';
    quickWrap.style.display = 'flex';
    sessionStorage.removeItem(SESSION_KEY);
    fetch('/api/copilot/memory/clear', {method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}).catch(()=>{});
  };

  // ── SEND MESSAGE ──
  const input = document.getElementById('cp-input');
  const sendBtn = document.getElementById('cp-send');
  sendBtn.onclick = () => sendMessage(input.value);
  input.onkeydown = (e) => { if(e.key==='Enter' && !e.shiftKey){e.preventDefault(); sendMessage(input.value);} };

  let sending = false;

  async function sendMessage(msg) {
    msg = (msg || '').trim();
    if (!msg || sending) return;
    sending = true;
    sendBtn.disabled = true;
    input.value = '';
    switchTab('chat');

    const body = document.getElementById('cp-body');
    const empty = body.querySelector('.cp-empty');
    if (empty) empty.remove();
    quickWrap.style.display = 'none';

    appendBubble('user', msg);
    const typingId = 'cp-typing-' + Date.now();
    body.innerHTML += `<div class="cp-msg cp-msg-ai" id="${typingId}"><div class="cp-typing"><span></span><span></span><span></span></div></div>`;
    body.scrollTop = body.scrollHeight;

    try {
      const r = await fetch('/api/copilot/chat', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({message: msg, page_type: PAGE_TYPE, page_id: PAGE_ID})
      });
      const d = await r.json();
      const typing = document.getElementById(typingId);
      if (typing) typing.remove();

      if (d.success) {
        appendBubble('ai', d.message, d.actions);
        saveHistory();
      } else {
        appendBubble('ai', d.error || 'Something went wrong', null, true);
      }
    } catch(e) {
      const typing = document.getElementById(typingId);
      if (typing) typing.remove();
      appendBubble('ai', 'Network error. Try again.', null, true);
    }

    body.scrollTop = body.scrollHeight;
    sending = false;
    sendBtn.disabled = false;
  }

  function appendBubble(type, text, actions, isError) {
    const body = document.getElementById('cp-body');
    if (type === 'user') {
      body.innerHTML += `<div class="cp-msg cp-msg-user"><div class="cp-bubble">${esc(text)}</div></div>`;
    } else {
      let html = `<div class="cp-msg cp-msg-ai"><div class="cp-bubble" ${isError?'style="color:#dc2626;border-color:#fecaca;"':''}>${formatMsg(text)}</div>`;
      if (actions && actions.length) {
        html += '<div class="cp-actions">';
        actions.forEach(a => {
          const cls = isConfirmAction(a.type) ? 'confirm' : 'safe';
          html += `<button class="cp-action-btn ${cls}" data-action='${esc(JSON.stringify(a))}'>${esc(a.label)}</button>`;
        });
        html += '</div>';
      }
      html += '</div>';
      body.innerHTML += html;
    }
  }

  // ── ALERTS ──
  let alertsLoaded = false;

  async function loadAlerts() {
    try {
      const r = await fetch('/api/copilot/alerts');
      const d = await r.json();
      if (!d.success) return;
      const alerts = d.alerts || [];
      const badge = document.getElementById('fab-badge');
      const dot = document.getElementById('alert-dot');
      const wrap = document.getElementById('cp-alerts-wrap');
      const emptyEl = document.getElementById('cp-alerts-empty');

      if (alerts.length > 0) {
        badge.textContent = alerts.length;
        badge.classList.add('show');
        dot.style.display = 'inline-block';
        if (emptyEl) emptyEl.style.display = 'none';
        // Render alerts only if fresh
        if (!alertsLoaded) {
          wrap.innerHTML = '';
          alerts.forEach(a => {
            const severity = a.severity === 'critical' ? '' : (a.severity === 'warning' ? 'warn' : 'info');
            const icon = a.severity === 'critical' ? '🔴' : (a.severity === 'warning' ? '🟡' : '🔵');
            wrap.innerHTML += `
              <div class="cp-alert-card ${severity}" data-alert-id="${a.id||''}">
                <div class="alert-icon">${icon}</div>
                <div class="alert-body">
                  <div class="alert-title">${esc(a.title || a.type)}</div>
                  <div class="alert-desc">${esc(a.message)}</div>
                </div>
                <div class="alert-dismiss" onclick="this.closest('.cp-alert-card').remove()"><i class="fas fa-times"></i></div>
              </div>`;
          });
          alertsLoaded = true;
        }
      } else {
        badge.classList.remove('show');
        dot.style.display = 'none';
        wrap.innerHTML = '<div class="cp-alerts-empty"><i class="fas fa-check-circle" style="font-size:20px;color:#10b981;display:block;margin-bottom:8px;"></i>All clear — no active alerts</div>';
      }
    } catch(e) {}
  }

  // Load alerts on page load
  setTimeout(loadAlerts, 2000);
  setInterval(loadAlerts, 60000);

  // ── ACTION EXECUTION ──
  document.body.addEventListener('click', async function(e) {
    const btnEl = e.target.closest('.cp-action-btn');
    if (!btnEl) return;
    let actionData;
    try { actionData = JSON.parse(btnEl.getAttribute('data-action')); } catch(ex) { return; }
    if (!actionData) return;

    if (isConfirmAction(actionData.type)) {
      if (!confirm(`Execute: ${actionData.label}?`)) return;
    }

    btnEl.disabled = true;
    btnEl.textContent = '⏳ Running...';

    try {
      const r = await fetch('/api/copilot/action', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action_type: actionData.type, params: actionData.params || {}, page_type: PAGE_TYPE, page_id: PAGE_ID})
      });
      const d = await r.json();
      if (d.success) {
        if (actionData.type === 'draft_reply' && d.draft) {
          const ta = document.getElementById('replyDraft');
          if (ta) ta.value = d.draft;
        }
        btnEl.textContent = '✓ ' + (d.message ? d.message.substring(0, 30) : 'Done');
        btnEl.style.borderColor = '#bbf7d0'; btnEl.style.color = '#15803d'; btnEl.style.background = '#f0fdf4';
      } else {
        btnEl.textContent = '✗ ' + (d.error || 'Failed');
        btnEl.style.borderColor = '#fecaca'; btnEl.style.color = '#dc2626';
      }
    } catch(ex) {
      btnEl.textContent = '✗ Network error';
      btnEl.style.borderColor = '#fecaca'; btnEl.style.color = '#dc2626';
    }
  });

  // ── HISTORY PERSISTENCE ──
  function saveHistory() {
    const body = document.getElementById('cp-body');
    sessionStorage.setItem(SESSION_KEY, body.innerHTML);
  }

  function restoreHistory() {
    const saved = sessionStorage.getItem(SESSION_KEY);
    if (saved) {
      const body = document.getElementById('cp-body');
      body.innerHTML = saved;
      quickWrap.style.display = 'none';
    }
  }
  restoreHistory();

  // ── HELPERS ──
  function isConfirmAction(type) {
    return ['retry_failed','pause_campaign','resume_campaign','cancel_campaign','send_reply','bulk_enrich'].includes(type);
  }

  function formatMsg(text) {
    let s = esc(text);
    // Markdown-like formatting
    s = s.replace(/\n/g, '<br>');
    s = s.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
    s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Lists: lines starting with - or •
    s = s.replace(/(?:^|<br>)[-•]\s(.+?)(?=<br>|$)/g, '<li>$1</li>');
    if (s.includes('<li>')) s = s.replace(/(<li>.*<\/li>)/g, '<ul>$1</ul>').replace(/<\/ul><ul>/g, '');
    return s;
  }

  function esc(s) { const d=document.createElement('div'); d.textContent=s||''; return d.innerHTML; }
})();
