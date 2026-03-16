// ============================================================
// Monitor mode initialization
// ============================================================
function initLayoutM() {
    // Trigger immediate poll to fill monitor data without waiting 10s
    pollStatus();
}

// ============================================================
// Monitor center updater
// ============================================================
function escHtml(str) {
    return String(str || '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function updateMonitor(data) {
    // Stats
    var elViewers  = document.getElementById('monitor-stat-viewers');
    var elComments = document.getElementById('monitor-stat-comments');
    var elActive   = document.getElementById('monitor-stat-active-rules');
    if (elViewers)  elViewers.textContent  = data.viewer_count || data.count || 0;
    if (elComments) elComments.textContent = data.total_comments || 0;

    var rules = data.rules_status;
    if (!rules) return;

    var activeCount = rules.filter(function(r) { return r.is_active; }).length;
    if (elActive) elActive.textContent = activeCount + '/' + rules.length;

    var list = document.getElementById('monitor-rules-list');
    if (!list) return;

    var html = '';
    rules.forEach(function(rule) {
        var cardCls = 'monitor-rule-card ' + (rule.is_active ? 'is-active' : 'is-sleeping');
        var statusHtml = rule.is_active
            ? '<span class="status-active">&#x2705; 稼働中</span>'
            : '<span class="monitor-sleep-text">&#x1F4A4; ' + escHtml(rule.reason) + '</span>';
        var footerHtml = '';
        if (rule.is_active) {
            footerHtml = '<div class="monitor-rule-footer">' +
                '<span class="monitor-timer">&#x23F0; ' + escHtml(rule.next_run) + '</span>' +
                (rule.remaining_comments > 0
                    ? '<span class="monitor-remaining">&#x1F4AC; あと' + rule.remaining_comments + 'コメント</span>'
                    : '<span style="color:var(--success); font-size:0.85em;">&#x2714; 条件OK</span>') +
                '</div>';
        }
        html += '<div class="' + cardCls + '">' +
            '<div class="monitor-rule-top">' +
                '<span class="monitor-rule-name">' + escHtml(rule.name) +
                    '<span class="monitor-rule-game">' + escHtml(rule.game) + '</span>' +
                '</span>' +
                statusHtml +
            '</div>' +
            '<div class="monitor-rule-msg">' + escHtml(rule.message) + '</div>' +
            footerHtml +
            '</div>';
    });

    if (!html) {
        html = '<div style="text-align:center; color:var(--text-secondary); padding:20px;">ルールが設定されていません</div>';
    }
    list.innerHTML = html;
}

// ============================================================
// Top bar updater
// ============================================================
function updateTopBar(data) {
    var gameEl    = document.getElementById('top-game');
    var viewerEl  = document.getElementById('top-viewers');
    var badge     = document.getElementById('live-badge');
    if (gameEl)   gameEl.textContent   = data.current_game || '';
    if (viewerEl) viewerEl.textContent = '\uD83D\uDC41 ' + (data.viewer_count || data.count || 0);
    if (badge) {
        badge.textContent = data.is_live ? '\u25CFLIVE' : '\u25CFOFF';
        badge.className   = data.is_live ? 'live-on' : 'live-off';
    }
}

// ============================================================
// Dashboard polling (10s interval + immediate on load)
// ============================================================
function pollStatus() {
    fetch('/api/status').then(function(r) { return r.json(); }).then(function(data) {
        updateTopBar(data);
        updateMonitor(data);

        var logContainer = document.getElementById('log-container');
        if (logContainer) {
            logContainer.innerHTML = data.logs.map(function(l) {
                return '<div>' + l + '</div>';
            }).join('');
        }

        var countSpan = document.getElementById('viewer-count-span');
        if (countSpan) {
            countSpan.innerText = '(' + (data.viewer_count || data.count) + '\u4EBA)';
        }

        var tb = document.getElementById('viewer-tbody');
        if (tb) {
            if (data.viewers.length === 0) {
                tb.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--text-secondary);">\u73FE\u5728\u306A\u3057 / \u30AA\u30D5\u30E9\u30A4\u30F3</td></tr>';
            } else {
                tb.innerHTML = data.viewers.map(function(v) {
                    return '<tr>' +
                        '<td><b>' + v.name + '</b></td>' +
                        '<td>' + v.follow_status + '</td>' +
                        '<td>' + v.duration + '</td>' +
                        '<td>' + v.total + '\u56DE</td>' +
                        '<td><button class="btn-memo" title="' + v.memo.replace(/"/g, '&quot;') + '" onclick="openMemoModal(\'' + v.uid + '\', \'' + v.name + '\', \'' + v.memo.replace(/'/g, "\\'") + '\')">\uD83D\uDCDD</button></td>' +
                        '<td><button class="btn-so" onclick="shoutout(\'' + v.login + '\', \'' + v.name + '\')">SO</button></td>' +
                        '</tr>';
                }).join('');
            }
        }
    }).catch(function(e) { console.error(e); });
}

function pollHistory() {
    fetch('/api/history').then(function(r) { return r.json(); }).then(function(data) {
        var table = document.getElementById('historyTable');
        var tbody = document.getElementById('history-tbody');
        if (!tbody || !table) return;
        var sortIdx = -1, sortDir = null;
        var headers = table.getElementsByTagName('TH');
        for (var i = 0; i < headers.length; i++) {
            if (headers[i].classList.contains('asc'))  { sortIdx = i; sortDir = 'asc';  break; }
            if (headers[i].classList.contains('desc')) { sortIdx = i; sortDir = 'desc'; break; }
        }
        tbody.innerHTML = data.map(function(v) {
            return '<tr>' +
                '<td>' + v.name + ' <br><span style="font-size:0.8em; color:var(--text-secondary);">(' + v.login + ')</span></td>' +
                '<td data-sort="' + v.follow_sort + '">' + (v.is_follower
                    ? '<div class="follow-status">\u2705</div><div style="font-size:0.75em; color:var(--text-secondary);">' + v.followed_at + '~</div>'
                    : '-') + '</td>' +
                '<td data-sort="' + v.total_visits + '">' + v.total_visits + '\u56DE</td>' +
                '<td data-sort="' + v.total_duration_raw + '">' + v.total_duration + '</td>' +
                '<td data-sort="' + v.total_comments + '">' + v.total_comments + '\u56DE</td>' +
                '<td data-sort="' + v.total_bits + '" class="' + (v.total_bits ? 'bits-count' : '') + '">' + v.total_bits + '</td>' +
                '<td data-sort="' + (v.is_sub ? 1 : 0) + '" class="' + (v.is_sub ? 'sub-active' : '') + '">' + (v.is_sub ? '\u2B50' : '-') + '</td>' +
                '<td data-sort="' + v.streak + '">' + (v.streak > 1 ? '\uD83D\uDD25' + v.streak : '-') + '</td>' +
                '<td><div class="memo-text">' + v.memo + '</div>' +
                '<button class="btn-memo" onclick="openMemoModal(\'' + v.uid + '\', \'' + v.name + '\', \'' + v.memo_esc + '\')">\uD83D\uDCDD</button></td>' +
                '<td data-sort="' + v.last_seen_sort + '">' + v.last_seen_str + '</td>' +
                '</tr>';
        }).join('');
        if (sortIdx !== -1 && sortDir) {
            sortTable('historyTable', sortIdx, sortDir);
        }
    }).catch(function(e) { console.error(e); });
}

// Start polling
setInterval(function() { pollStatus(); pollHistory(); }, 10000);
// Initial load (runs after DOMContentLoaded via common.js layout restore, but initLayoutM also calls pollStatus)
document.addEventListener('DOMContentLoaded', function() {
    pollStatus();
    pollHistory();
});
