// API Helper
async function api(path, method = 'POST', payload) {
  const opts = { method, headers: { 'Content-Type': 'application/json' } };
  if (payload) opts.body = JSON.stringify(payload);
  const res = await fetch(path, opts);
  if (!res.ok) {
    let data;
    try { data = await res.json(); } catch (e) { throw new Error('Network error'); }
    throw new Error(data.detail || data.error || 'Error');
  }
  return res.status === 204 ? null : res.json();
}

// Global data
let guildData = {};

// ==================== INITIALIZATION ====================
window.addEventListener('load', () => {
  setupTabNavigation();
  setupEventListeners();
  loadUser();
});

function setupTabNavigation() {
  document.querySelectorAll('.nav-item').forEach(item => {
    item.addEventListener('click', (e) => {
      e.preventDefault();
      const tabName = item.dataset.tab;
      showTab(tabName);
      
      // Update active nav item
      document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
      item.classList.add('active');
      
      // Refresh data if needed
      if (tabName === 'dashboard') loadStats();
      if (tabName === 'logs') loadLogs();
    });
  });
  
  // Set initial active state
  document.querySelector('[data-tab="dashboard"]')?.classList.add('active');
}

function showTab(tabName) {
  document.querySelectorAll('.tab-content').forEach(tab => tab.classList.remove('active'));
  const tab = document.getElementById(tabName);
  if (tab) tab.classList.add('active');
  
  // Update header
  const titles = {
    dashboard: '📊 Dashboard',
    moderation: '🔨 Moderation',
    members: '👥 Mitglieder Management',
    settings: '⚙️ Einstellungen',
    logs: '📋 Audit Log'
  };
  document.getElementById('page-title').textContent = titles[tabName] || 'Dashboard';
}

// ==================== USER MANAGEMENT ====================
async function loadUser() {
  try {
    const res = await fetch('/api/me');
    if (!res.ok) return showLoggedOut();
    const user = await res.json();
    showUser(user);
    await loadGuildData();
    loadStats();
  } catch (e) {
    showLoggedOut();
  }
}

function showUser(user) {
  const box = document.getElementById('userbox');
  box.innerHTML = `
    <div style="font-weight:700">${user.username}</div>
    <div style="font-size:0.75rem;opacity:0.7">ID: ${user.id}</div>
  `;
  document.getElementById('login-btn').style.display = 'none';
  document.getElementById('logout-btn').style.display = 'block';
  document.getElementById('logout-btn').onclick = () => location.href = '/auth/logout';
}

function showLoggedOut() {
  document.getElementById('userbox').innerHTML = '<div style="font-size:0.875rem">Not logged in</div>';
  document.getElementById('login-btn').style.display = 'block';
  document.getElementById('logout-btn').style.display = 'none';
}

// ==================== GUILD DATA ====================
async function loadGuildData() {
  try {
    const res = await fetch('/api/guild/info');
    if (!res.ok) return;
    guildData = await res.json();
    
    // Populate all dropdowns
    populateDropdown('warn-member', guildData.members, 'display_name');
    populateDropdown('mute-member', guildData.members, 'display_name');
    populateDropdown('unmute-member', guildData.members, 'display_name');
    populateDropdown('role-member', guildData.members, 'display_name');
    populateDropdown('message-channel', guildData.channels, 'name');
    populateDropdown('ban-member', guildData.members, 'display_name');
    populateDropdown('ban-mod-log', guildData.channels, 'name');
    populateDropdown('info-member', guildData.members, 'display_name');
    populateDropdown('clear-warn-member', guildData.members, 'display_name');
    populateDropdown('raid-owner', guildData.roles, 'name');
    populateDropdown('raid-alert', guildData.channels, 'name');
    populateDropdown('role-id', guildData.roles, 'name');
    populateDropdown('auto-roles', guildData.roles, 'name');
  } catch (e) {
    console.error('Failed to load guild data:', e);
  }
}

function populateDropdown(selectId, items, labelKey) {
  const select = document.getElementById(selectId);
  if (!select) return;
  
  // Keep existing placeholder/options
  const firstOption = select.querySelector('option:first-child');
  while (select.children.length > 1) select.removeChild(select.lastChild);
  
  items.forEach(item => {
    const option = document.createElement('option');
    option.value = item.id;
    option.textContent = (item[labelKey] || item.name) + (item.avatar ? ' 👤' : '');
    select.appendChild(option);
  });
}

// ==================== STATISTICS ====================
async function loadStats() {
  try {
    const res = await fetch('/api/bot/stats');
    if (!res.ok) return;
    const stats = await res.json();
    
    document.getElementById('stat-members').textContent = stats.member_count || '-';
    document.getElementById('stat-online').textContent = stats.online_members || '-';
    document.getElementById('stat-channels').textContent = (stats.text_channels + stats.voice_channels) || '-';
    document.getElementById('stat-text').textContent = stats.text_channels || '-';
    document.getElementById('stat-roles').textContent = stats.roles_count || '-';
    document.getElementById('stat-warnings').textContent = stats.total_warnings || '0';
  } catch (e) {
    console.error('Failed to load stats:', e);
  }
}

// ==================== MODERATION ACTIONS ====================
function setupEventListeners() {
  // Warn
  if (document.getElementById('warn-btn')) {
    document.getElementById('warn-btn').addEventListener('click', async () => {
      const member = document.getElementById('warn-member').value;
      const reason = document.getElementById('warn-reason').value.trim();
      const status = document.getElementById('warn-status');
      
      if (!member || !reason) {
        setStatus(status, '❌ Please fill all fields', 'error');
        return;
      }
      
      try {
        await api('/api/moderation/warn', 'POST', { member_id: member, reason });
        setStatus(status, '✅ Warning issued', 'success');
        document.getElementById('warn-reason').value = '';
      } catch (e) {
        setStatus(status, '❌ ' + e.message, 'error');
      }
    });
  }

  // Mute
  if (document.getElementById('mute-btn')) {
    document.getElementById('mute-btn').addEventListener('click', async () => {
      const member = document.getElementById('mute-member').value;
      const duration = parseInt(document.getElementById('mute-duration').value) || 3600;
      const reason = document.getElementById('mute-reason').value.trim();
      const status = document.getElementById('mute-status');
      
      if (!member) {
        setStatus(status, '❌ Select member', 'error');
        return;
      }
      
      try {
        await api('/api/moderation/mute', 'POST', { member_id: member, duration_seconds: duration, reason });
        setStatus(status, `✅ Muted for ${duration}s`, 'success');
      } catch (e) {
        setStatus(status, '❌ ' + e.message, 'error');
      }
    });
  }

  // Unmute
  if (document.getElementById('unmute-btn')) {
    document.getElementById('unmute-btn').addEventListener('click', async () => {
      const member = document.getElementById('unmute-member').value;
      const status = document.getElementById('unmute-status');
      
      if (!member) {
        setStatus(status, '❌ Select member', 'error');
        return;
      }
      
      try {
        await api('/api/moderation/unmute', 'POST', { member_id: member });
        setStatus(status, '✅ Unmuted', 'success');
      } catch (e) {
        setStatus(status, '❌ ' + e.message, 'error');
      }
    });
  }

  // Add Role
  if (document.getElementById('add-role-btn')) {
    document.getElementById('add-role-btn').addEventListener('click', async () => {
      const member = document.getElementById('role-member').value;
      const role = document.getElementById('role-id').value;
      const status = document.getElementById('role-status');
      
      if (!member || !role) {
        setStatus(status, '❌ Select member and role', 'error');
        return;
      }
      
      try {
        await api('/api/roles/add', 'POST', { member_id: member, role_id: role });
        setStatus(status, '✅ Role assigned', 'success');
      } catch (e) {
        setStatus(status, '❌ ' + e.message, 'error');
      }
    });
  }

  // Send Message
  if (document.getElementById('send-msg-btn')) {
    document.getElementById('send-msg-btn').addEventListener('click', async () => {
      const channel = document.getElementById('message-channel').value;
      const content = document.getElementById('message-content').value.trim();
      const status = document.getElementById('message-status');
      
      if (!channel || !content) {
        setStatus(status, '❌ Select channel and enter message', 'error');
        return;
      }
      
      try {
        await api('/api/message/send', 'POST', { channel_id: channel, content });
        setStatus(status, '✅ Message sent', 'success');
        document.getElementById('message-content').value = '';
      } catch (e) {
        setStatus(status, '❌ ' + e.message, 'error');
      }
    });
  }

  // Ban
  if (document.getElementById('ban-btn')) {
    document.getElementById('ban-btn').addEventListener('click', async () => {
      const member = document.getElementById('ban-member').value;
      const reason = document.getElementById('ban-reason').value.trim();
      const modLog = document.getElementById('ban-mod-log').value;
      const status = document.getElementById('ban-status');
      
      if (!member || !reason) {
        setStatus(status, '❌ Select member and enter reason', 'error');
        return;
      }
      
      try {
        await api('/api/moderation/ban', 'POST', { 
          member_id: member, 
          reason, 
          mod_log_channel_id: modLog || null 
        });
        setStatus(status, '✅ User banned', 'success');
      } catch (e) {
        setStatus(status, '❌ ' + e.message, 'error');
      }
    });
  }

  // Create Ticket
  if (document.getElementById('ticket-btn')) {
    document.getElementById('ticket-btn').addEventListener('click', async () => {
      const title = document.getElementById('ticket-title').value.trim();
      const status = document.getElementById('ticket-status');
      
      if (!title) {
        setStatus(status, '❌ Enter ticket title', 'error');
        return;
      }
      
      try {
        const data = await api('/api/tickets', 'POST', { title });
        setStatus(status, `✅ Ticket created: #${data.channel_id}`, 'success');
        document.getElementById('ticket-title').value = '';
      } catch (e) {
        setStatus(status, '❌ ' + e.message, 'error');
      }
    });
  }

  // Raid Protection
  if (document.getElementById('raid-btn')) {
    document.getElementById('raid-btn').addEventListener('click', async () => {
      const enable = document.getElementById('raid-enable').checked;
      const owner = document.getElementById('raid-owner').value;
      const alert = document.getElementById('raid-alert').value;
      const status = document.getElementById('raid-status');
      
      if (enable && !owner) {
        setStatus(status, '❌ Select owner role', 'error');
        return;
      }
      
      try {
        await api('/api/raid', 'POST', {
          enable,
          owner_role_id: owner || null,
          alert_channel_id: alert || null,
          close_voice: true,
          restrict_text: true
        });
        setStatus(status, enable ? '✅ Raid protection enabled' : '✅ Raid protection disabled', 'success');
      } catch (e) {
        setStatus(status, '❌ ' + e.message, 'error');
      }
    });
  }

  // Member Info
  if (document.getElementById('info-btn')) {
    document.getElementById('info-btn').addEventListener('click', async () => {
      const member = document.getElementById('info-member').value;
      const container = document.getElementById('member-info-container');
      
      if (!member) {
        container.innerHTML = '';
        return;
      }
      
      try {
        const info = await api(`/api/moderation/member/${member}`, 'GET');
        const warnings = info.warnings || [];
        
        container.innerHTML = `
          <div class="info-panel">
            <div class="info-row">
              <span class="info-label">User</span>
              <span class="info-value">${info.display_name || 'Unknown'}</span>
            </div>
            <div class="info-row">
              <span class="info-label">Warnings</span>
              <span class="info-value">${warnings.length}</span>
            </div>
            <div class="info-row">
              <span class="info-label">Muted</span>
              <span class="info-value">${info.is_muted ? '✅ Yes' : '❌ No'}</span>
            </div>
            ${warnings.length > 0 ? `
              <div style="margin-top:1rem;padding-top:1rem;border-top:1px solid var(--border);">
                <strong>Warnings:</strong>
                ${warnings.map((w, i) => `
                  <div style="font-size:0.75rem;margin-top:0.5rem;padding:0.5rem;background:rgba(239,68,68,0.1);border-radius:0.375rem;">
                    <strong>#${i + 1}:</strong> ${w.reason}<br/>
                    <span style="opacity:0.7">${new Date(w.timestamp).toLocaleString()}</span>
                  </div>
                `).join('')}
              </div>
            ` : ''}
          </div>
        `;
      } catch (e) {
        container.innerHTML = `<div class="info-panel" style="color:var(--danger)">Error: ${e.message}</div>`;
      }
    });
  }

  // Clear Warnings
  if (document.getElementById('clear-warn-btn')) {
    document.getElementById('clear-warn-btn').addEventListener('click', async () => {
      const member = document.getElementById('clear-warn-member').value;
      const status = document.getElementById('clear-warn-status');
      
      if (!member) {
        setStatus(status, '❌ Select member', 'error');
        return;
      }
      
      try {
        await api('/api/moderation/clear-warnings', 'POST', { member_id: member });
        setStatus(status, '✅ Warnings cleared', 'success');
      } catch (e) {
        setStatus(status, '❌ ' + e.message, 'error');
      }
    });
  }

  // Activity
  if (document.getElementById('activity-btn')) {
    document.getElementById('activity-btn').addEventListener('click', async () => {
      const text = document.getElementById('activity-text').value.trim();
      const type = document.getElementById('activity-type').value;
      const status = document.getElementById('activity-status');
      
      if (!text) {
        setStatus(status, '❌ Enter activity text', 'error');
        return;
      }
      
      try {
        await api('/api/bot/activity', 'POST', { text, type });
        setStatus(status, `✅ Activity set to "${type} ${text}"`, 'success');
        document.getElementById('activity-text').value = '';
      } catch (e) {
        setStatus(status, '❌ ' + e.message, 'error');
      }
    });
  }

  // Random Voice
  if (document.getElementById('random-voice-btn')) {
    document.getElementById('random-voice-btn').addEventListener('click', async () => {
      const enabled = document.getElementById('random-voice-enable').checked;
      const interval = parseInt(document.getElementById('random-voice-interval').value) || 600;
      const sound = document.getElementById('random-voice-sound').value.trim();
      const status = document.getElementById('random-voice-status');
      
      try {
        await api('/api/bot/random-voice', 'POST', { enabled, interval, sound_file: sound || null });
        setStatus(status, enabled ? `✅ Enabled (${interval}s interval)` : '✅ Disabled', 'success');
      } catch (e) {
        setStatus(status, '❌ ' + e.message, 'error');
      }
    });
  }

  // Auto Roles
  if (document.getElementById('auto-roles-btn')) {
    document.getElementById('auto-roles-btn').addEventListener('click', async () => {
      const select = document.getElementById('auto-roles');
      const roleIds = Array.from(select.selectedOptions).map(opt => opt.value);
      const status = document.getElementById('auto-roles-status');
      
      try {
        await api('/api/bot/auto-roles', 'POST', { role_ids: roleIds });
        setStatus(status, `✅ ${roleIds.length} role(s) set as auto-roles`, 'success');
      } catch (e) {
        setStatus(status, '❌ ' + e.message, 'error');
      }
    });
  }

  // Logs
  if (document.getElementById('log-refresh-btn')) {
    document.getElementById('log-refresh-btn').addEventListener('click', loadLogs);
  }
}

function setStatus(elem, message, type) {
  elem.textContent = message;
  elem.className = 'status ' + type;
}

// ==================== LOGS ====================
async function loadLogs() {
  try {
    const limit = parseInt(document.getElementById('log-limit').value) || 50;
    const res = await api(`/api/moderation/log?limit=${limit}`, 'GET');
    const container = document.getElementById('log-container');
    
    if (!res.log || res.log.length === 0) {
      container.innerHTML = '<div style="padding:1rem;text-align:center;opacity:0.5">No actions logged yet</div>';
      return;
    }
    
    container.innerHTML = res.log.map(entry => `
      <div class="log-entry">
        <div class="log-action">${entry.action}</div>
        <div>${entry.reason || 'No reason'}</div>
        <div class="log-time">${new Date(entry.timestamp).toLocaleString()}</div>
      </div>
    `).join('');
  } catch (e) {
    console.error('Failed to load logs:', e);
  }
}


async function loadUser(){
  try{
    const res = await fetch('/api/me');
    if(!res.ok) return showLoggedOut();
    const user = await res.json();
    showUser(user);
    // Load guild data after user is loaded
    loadGuildData();
  }catch(e){showLoggedOut();}
}

async function loadGuildData(){
  try{
    const res = await fetch('/api/guild/info');
    if(!res.ok) return;
    const data = await res.json();
    
    // Populate dropdowns
    populateDropdown('role-member', data.members, 'display_name');
    populateDropdown('role-id', data.roles, 'name');
    populateDropdown('message-channel', data.channels, 'name');
    populateDropdown('ban-member', data.members, 'display_name');
    populateDropdown('ban-mod-log', data.channels, 'name');
    populateDropdown('raid-owner', data.roles, 'name');
    populateDropdown('raid-alert', data.channels, 'name');
    
    // Restore last selections from localStorage
    restoreSelections();
  }catch(e){console.error('Failed to load guild data:', e);}
}

function populateDropdown(selectId, items, labelKey){
  const select = document.getElementById(selectId);
  if(!select) return;
  items.forEach(item => {
    const option = document.createElement('option');
    option.value = item.id;
    // For users with avatars, add visual hint in text
    if(item.avatar){
      option.textContent = `👤 ${item[labelKey] || item.name}`;
    } else {
      option.textContent = item[labelKey] || item.name;
    }
    select.appendChild(option);
  });
}

function saveSelection(selectId){
  const select = document.getElementById(selectId);
  if(select) localStorage.setItem(`selected_${selectId}`, select.value);
}

function restoreSelections(){
  ['role-member', 'role-id', 'message-channel', 'ban-member', 'ban-mod-log', 'raid-owner', 'raid-alert'].forEach(id => {
    const select = document.getElementById(id);
    if(select){
      const saved = localStorage.getItem(`selected_${id}`);
      if(saved) select.value = saved;
      select.addEventListener('change', () => saveSelection(id));
    }
  });
}

function showUser(user){
  document.getElementById('login-btn').style.display='none';
  const box = document.getElementById('userbox');
  box.innerHTML = `
    <div class="userbox">
      <img src="https://cdn.discordapp.com/avatars/${user.id}/${user.avatar}.png" alt="avatar" onerror="this.style.display='none'"/>
      <div>
        <div style="font-weight:700">${user.username}#${user.discriminator}</div>
        <div class="small">ID: ${user.id}</div>
      </div>
    </div>
  `;
  document.getElementById('logout-btn').style.display='inline-block';
}

function showLoggedOut(){
  document.getElementById('userbox').innerHTML = '<div class="small">Nicht eingeloggt</div>';
  document.getElementById('login-btn').style.display='inline-block';
  document.getElementById('logout-btn').style.display='none';
}

async function addRole(){
  const member = document.getElementById('role-member').value;
  const role = document.getElementById('role-id').value;
  if(!member || member === ''){document.getElementById('role-status').textContent='❌ Bitte wähle Mitglied aus';return}
  if(!role || role === ''){document.getElementById('role-status').textContent='❌ Bitte wähle Rolle aus';return}
  try{await api('/api/roles/add','POST',{member_id:member,role_id:role});document.getElementById('role-status').textContent='✅ Rolle vergeben';}
  catch(e){document.getElementById('role-status').textContent='❌ '+e.message}
}

async function sendMessage(){
  const ch = document.getElementById('message-channel').value;
  const content = document.getElementById('message-content').value.trim();
  if(!ch || ch === ''){document.getElementById('message-status').textContent='❌ Bitte wähle Kanal aus';return}
  if(!content){document.getElementById('message-status').textContent='❌ Bitte gib eine Nachricht ein';return}
  try{await api('/api/message/send','POST',{channel_id:ch,content});document.getElementById('message-content').value='';document.getElementById('message-status').textContent='✅ Nachricht gesendet';}
  catch(e){document.getElementById('message-status').textContent='❌ '+e.message}
}

async function banUser(){
  const member = document.getElementById('ban-member').value;
  const reason = document.getElementById('ban-reason').value.trim();
  const modLog = document.getElementById('ban-mod-log').value;
  if(!member || member === ''){document.getElementById('ban-status').textContent='❌ Bitte wähle Mitglied aus';return}
  if(!reason){document.getElementById('ban-status').textContent='❌ Bitte gib einen Grund ein';return}
  try{await api('/api/moderation/ban','POST',{member_id:member,reason,mod_log_channel_id: modLog || null});document.getElementById('ban-status').textContent='✅ User gebannt + DM gesendet';}
  catch(e){document.getElementById('ban-status').textContent='❌ '+e.message}
}

async function createTicket(){
  const title = document.getElementById('ticket-title').value.trim();
  try{const data = await api('/api/tickets','POST',{title});document.getElementById('ticket-status').textContent=`Ticket erstellt: #${data.channel_id}`}
  catch(e){document.getElementById('ticket-status').textContent=e.message}
}

function logout(){location.href='/auth/logout'}

window.addEventListener('load',()=>{
  document.getElementById('add-role-btn').addEventListener('click',addRole);
  document.getElementById('send-msg-btn').addEventListener('click',sendMessage);
  document.getElementById('ban-btn').addEventListener('click',banUser);
  document.getElementById('ticket-btn').addEventListener('click',createTicket);
  document.getElementById('logout-btn').addEventListener('click',logout);
  loadUser();
});

async function setRaid(){
  const enable = document.getElementById('raid-enable').checked;
  const owner = document.getElementById('raid-owner').value;
  const alert = document.getElementById('raid-alert').value;
  const closeVoice = document.getElementById('raid-voice').checked;
  const restrictText = document.getElementById('raid-text').checked;
  if(enable && (!owner || owner === '')){document.getElementById('raid-status').textContent='❌ Bitte wähle Owner-Rolle aus';return}
  try{
    await api('/api/raid','POST',{enable, owner_role_id: owner || null, alert_channel_id: alert || null, close_voice: closeVoice, restrict_text: restrictText});
    document.getElementById('raid-status').textContent = enable ? '✅ Raid-Schutz aktiviert' : '✅ Raid-Schutz deaktiviert';
  }catch(e){document.getElementById('raid-status').textContent = '❌ '+e.message}
}

// bind raid button if present
window.addEventListener('load',()=>{
  const raidBtn = document.getElementById('raid-btn');
  if(raidBtn) raidBtn.addEventListener('click', setRaid);
  
  const testBtn = document.getElementById('test-ban-btn');
  if(testBtn) testBtn.addEventListener('click', testBanDm);
  
  const activityBtn = document.getElementById('activity-btn');
  if(activityBtn) activityBtn.addEventListener('click', setActivity);
  
  const randomVoiceBtn = document.getElementById('random-voice-btn');
  if(randomVoiceBtn) randomVoiceBtn.addEventListener('click', setRandomVoice);
});

async function testBanDm(){
  const userId = document.getElementById('test-user-id').value.trim();
  const reason = document.getElementById('test-reason').value.trim();
  const modLog = document.getElementById('test-mod-log').value.trim();
  if(!userId){document.getElementById('test-status').textContent='Bitte gib eine User ID ein';return}
  try{
    await api('/api/test/ban-dm','POST',{member_id: userId, reason, mod_log_channel_id: modLog || null});
    document.getElementById('test-status').textContent='✅ Test-DM gesendet (kein Ban!)';
  }catch(e){document.getElementById('test-status').textContent=e.message}
}

async function setActivity(){
  const text = document.getElementById('activity-text').value.trim();
  const type = document.getElementById('activity-type').value;
  if(!text){document.getElementById('activity-status').textContent='❌ Text erforderlich';return}
  try{
    await api('/api/bot/activity','POST',{text, type});
    document.getElementById('activity-status').textContent=`✅ Activity: "${type} ${text}"`;
    document.getElementById('activity-text').value='';
  }catch(e){document.getElementById('activity-status').textContent='❌ '+e.message}
}

async function setRandomVoice(){
  const enabled = document.getElementById('random-voice-enable').checked;
  const interval = parseInt(document.getElementById('random-voice-interval').value) || 600;
  const sound = document.getElementById('random-voice-sound').value.trim();
  if(interval < 60){document.getElementById('random-voice-status').textContent='❌ Minimum 60 Sekunden';return}
  try{
    await api('/api/bot/random-voice','POST',{enabled, interval, sound_file: sound || null});
    const status = enabled ? `✅ Aktiviert (Interval: ${interval}s)` : '✅ Deaktiviert';
    document.getElementById('random-voice-status').textContent = status;
  }catch(e){document.getElementById('random-voice-status').textContent='❌ '+e.message}
}
