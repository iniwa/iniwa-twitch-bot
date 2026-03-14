// ============================================================
// Sortable.js initialization (layout-a)
// ============================================================
function initSortable() {
    var grid = document.getElementById('main-grid');
    if (!grid || window._sortable) return;
    if (typeof Sortable === 'undefined') return;
    window._sortable = Sortable.create(grid, {
        animation: 150,
        ghostClass: 'sortable-ghost',
        dragClass: 'sortable-drag',
        filter: 'input, button, a, select, textarea, .no-drag',
        preventOnFilter: false,
        onEnd: saveCardOrder
    });
    restoreCardOrder();
}

function saveCardOrder() {
    var children = document.querySelectorAll('#main-grid > [data-card], #main-grid > #center-group');
    var order = Array.from(children).map(function(c) {
        return c.dataset.card || c.id;
    });
    localStorage.setItem('cardOrder', JSON.stringify(order));
}

function restoreCardOrder() {
    var saved = localStorage.getItem('cardOrder');
    if (!saved) return;
    try {
        var order = JSON.parse(saved);
        var grid = document.getElementById('main-grid');
        if (!grid) return;
        order.forEach(function(key) {
            var el = grid.querySelector('[data-card="' + key + '"]') ||
                     grid.querySelector('#' + key);
            if (el) grid.appendChild(el);
        });
    } catch(e) {}
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
        badge.textContent = data.bot_active ? '\u25CFLIVE' : '\u25CFOFF';
        badge.className   = data.bot_active ? 'live-on' : 'live-off';
    }
}

// ============================================================
// Dashboard polling (10s interval)
// ============================================================
setInterval(function() {
    fetch('/api/status').then(function(r) { return r.json(); }).then(function(data) {
        updateTopBar(data);

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
                        '<td><div class="memo-text">' + v.memo + '</div>' +
                        '<button class="btn-memo" onclick="openMemoModal(\'' + v.uid + '\', \'' + v.name + '\', \'' + v.memo.replace(/'/g, "\\'") + '\')">\uD83D\uDCDD</button></td>' +
                        '<td style="text-align:right;"><button class="btn-so" onclick="shoutout(\'' + v.login + '\', \'' + v.name + '\')">\uD83D\uDCE3 SO</button></td>' +
                        '</tr>';
                }).join('');
            }
        }
    }).catch(function(e) { console.error(e); });

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
}, 10000);
