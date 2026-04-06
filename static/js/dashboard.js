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
// Event panel updater
// ============================================================
function formatEventTime(isoStr) {
    if (!isoStr) return '';
    try {
        var d = new Date(isoStr);
        return d.toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit' });
    } catch (e) { return ''; }
}

function renderEvent(ev) {
    var icon = '', body = '', cls = '';
    var user = escHtml(ev.user || '');
    switch (ev.type) {
        case 'sub':
            icon = '\uD83C\uDF89';
            cls = 'event-type-sub';
            body = '<span class="event-user">' + user + '</span> <span class="event-detail">' +
                (ev.is_resub ? '\u518D\u30B5\u30D6' : '\u30B5\u30D6\u30B9\u30AF') +
                ' (' + escHtml(ev.plan || '') + ', ' + (ev.months || 1) + '\u30F6\u6708)</span>';
            break;
        case 'subgift':
            icon = '\uD83C\uDF81';
            cls = 'event-type-subgift';
            body = '<span class="event-user">' + user + '</span> <span class="event-detail">\u2192 ' +
                escHtml(ev.recipient || '') + ' \u306B\u30AE\u30D5\u30C8\u30B5\u30D6 (' + escHtml(ev.plan || '') + ')</span>';
            break;
        case 'submysterygift':
            icon = '\uD83C\uDF81';
            cls = 'event-type-submysterygift';
            body = '<span class="event-user">' + user + '</span> <span class="event-detail">' +
                (ev.count || 1) + '\u4EBA\u5206\u306E\u30AE\u30D5\u30C8\u30B5\u30D6\uFF01</span>';
            break;
        case 'raid':
            icon = '\uD83D\uDEA8';
            cls = 'event-type-raid';
            body = '<span class="event-user">' + user + '</span> <span class="event-detail">\u304C\u30EC\u30A4\u30C9 (' +
                (ev.count || 0) + '\u4EBA)</span>';
            break;
        case 'bits':
            icon = '\uD83D\uDCB0';
            cls = 'event-type-bits';
            body = '<span class="event-user">' + user + '</span> <span class="event-detail">' +
                (ev.amount || 0) + ' bits</span>';
            break;
        case 'follow':
            icon = '\uD83D\uDC9A';
            cls = 'event-type-follow';
            body = '<span class="event-user">' + user + '</span> <span class="event-detail">\u304C\u30D5\u30A9\u30ED\u30FC</span>';
            break;
        default:
            icon = '\u2139\uFE0F';
            body = '<span class="event-user">' + user + '</span> <span class="event-detail">' + escHtml(ev.type || '') + '</span>';
    }
    return '<div class="event-item ' + cls + '">' +
        '<span class="event-icon">' + icon + '</span>' +
        '<div class="event-body">' + body + '</div>' +
        '<span class="event-time">' + formatEventTime(ev.timestamp) + '</span>' +
        '</div>';
}

function updateEventPanel(events) {
    var feed = document.getElementById('event-feed');
    if (!feed) return;
    if (!events || events.length === 0) {
        feed.innerHTML = '<div style="text-align:center; color:var(--text-secondary); padding:20px; font-size:0.85em;">\u30A4\u30D9\u30F3\u30C8\u5F85\u6A5F\u4E2D...</div>';
        return;
    }
    feed.innerHTML = events.map(renderEvent).join('');
    var badge = document.getElementById('event-count-badge');
    if (badge) {
        badge.textContent = events.length;
        badge.style.display = events.length > 0 ? 'inline' : 'none';
    }
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
        updateEventPanel(data.events);

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
                    var followHtml = v.is_follower
                        ? '<span class="follow-status">\u2705</span> <span style="font-size:0.75em; color:#888;">' + escHtml(v.followed_at || '') + '</span>'
                        : '-';
                    var memoEsc = escHtml(v.memo || '');
                    return '<tr>' +
                        '<td><b>' + escHtml(v.name) + '</b></td>' +
                        '<td>' + followHtml + '</td>' +
                        '<td>' + escHtml(v.duration) + '</td>' +
                        '<td>' + v.total + '\u56DE</td>' +
                        '<td><button class="btn-memo" title="' + memoEsc + '" onclick="openMemoModal(\'' + escHtml(v.uid) + '\', \'' + escHtml(v.name) + '\', \'' + memoEsc.replace(/'/g, "\\'") + '\')">\uD83D\uDCDD</button></td>' +
                        '<td><button class="btn-so" onclick="shoutout(\'' + escHtml(v.login) + '\', \'' + escHtml(v.name) + '\')">SO</button></td>' +
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
            var memoEsc = escHtml(v.memo || '');
            var lastSeenHtml = v.is_active
                ? '<span class="watching-status">\u8996\u8074\u4E2D</span>'
                : escHtml(v.last_seen_str || '-');
            return '<tr>' +
                '<td>' + escHtml(v.name) + ' <br><span style="font-size:0.8em; color:var(--text-secondary);">(' + escHtml(v.login) + ')</span></td>' +
                '<td data-sort="' + v.follow_sort + '">' + (v.is_follower
                    ? '<div class="follow-status">\u2705</div><div style="font-size:0.75em; color:var(--text-secondary);">' + escHtml(v.followed_at) + '~</div>'
                    : '-') + '</td>' +
                '<td data-sort="' + v.total_visits + '">' + v.total_visits + '\u56DE</td>' +
                '<td data-sort="' + v.total_duration_raw + '">' + escHtml(v.total_duration) + '</td>' +
                '<td data-sort="' + v.total_comments + '">' + v.total_comments + '\u56DE</td>' +
                '<td data-sort="' + v.total_bits + '" class="' + (v.total_bits ? 'bits-count' : '') + '">' + v.total_bits + '</td>' +
                '<td data-sort="' + (v.is_sub ? 1 : 0) + '" class="' + (v.is_sub ? 'sub-active' : '') + '">' + (v.is_sub ? '\u2B50' : '-') + '</td>' +
                '<td data-sort="' + (v.total_sub_months || 0) + '">' + (v.total_sub_months ? v.total_sub_months : '-') + '</td>' +
                '<td data-sort="' + (v.total_gifts_given || 0) + '">' + (v.total_gifts_given ? '\uD83C\uDF81' + v.total_gifts_given : '-') + '</td>' +
                '<td data-sort="' + v.streak + '">' + (v.streak > 1 ? '\uD83D\uDD25' + v.streak : '-') + '</td>' +
                '<td><div class="memo-text">' + memoEsc + '</div>' +
                '<button class="btn-memo" onclick="openMemoModal(\'' + escHtml(v.uid) + '\', \'' + escHtml(v.name) + '\', \'' + memoEsc.replace(/'/g, "\\'") + '\')">\uD83D\uDCDD</button></td>' +
                '<td data-sort="' + v.last_seen_sort + '">' + lastSeenHtml + '</td>' +
                '</tr>';
        }).join('');
        if (sortIdx !== -1 && sortDir) {
            sortTable('historyTable', sortIdx, sortDir);
        }
    }).catch(function(e) { console.error(e); });
}

// ============================================================
// Vertical resize (viewer/event split)
// ============================================================
function initVerticalResize() {
    var handle = document.getElementById('resize-viewer-v');
    if (!handle) return;
    var upper = document.getElementById('viewer-upper');
    var lower = document.getElementById('event-lower');
    if (!upper || !lower) return;

    // Restore saved ratio
    var savedRatio = localStorage.getItem('viewer-split-ratio');
    if (savedRatio) {
        var r = parseFloat(savedRatio);
        upper.style.flex = r;
        lower.style.flex = 1 - r;
    }

    handle.addEventListener('mousedown', function(e) {
        e.preventDefault();
        handle.classList.add('dragging');
        var container = upper.parentElement;
        var containerRect = container.getBoundingClientRect();
        var handleHeight = handle.offsetHeight;
        var totalHeight = containerRect.height - handleHeight;

        function onMove(ev) {
            var offsetY = ev.clientY - containerRect.top;
            var ratio = Math.max(0.15, Math.min(0.85, offsetY / containerRect.height));
            upper.style.flex = ratio;
            lower.style.flex = 1 - ratio;
            localStorage.setItem('viewer-split-ratio', ratio);
        }
        function onUp() {
            handle.classList.remove('dragging');
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
        }
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    });
}

// Start polling
setInterval(function() { pollStatus(); pollHistory(); }, 10000);
// Initial load (runs after DOMContentLoaded via common.js layout restore, but initLayoutM also calls pollStatus)
document.addEventListener('DOMContentLoaded', function() {
    pollStatus();
    pollHistory();
    initVerticalResize();
});
