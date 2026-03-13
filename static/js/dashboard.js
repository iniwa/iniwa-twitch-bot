// Dashboard polling (10s interval)
setInterval(function() {
    fetch('/api/status').then(function(r) { return r.json(); }).then(function(data) {
        var logContainer = document.getElementById('log-container');
        if (logContainer) {
            logContainer.innerHTML = data.logs.map(function(l) { return '<div>' + l + '</div>'; }).join('');
        }
        var countSpan = document.getElementById('viewer-count-span');
        if (countSpan) {
            countSpan.innerText = '(' + data.count + '人)';
        }
        var tb = document.getElementById('viewer-tbody');
        if (tb) {
            if (data.viewers.length === 0) {
                tb.innerHTML = '<tr><td colspan="6" style="text-align:center; color:#999;">現在なし / オフライン</td></tr>';
            } else {
                tb.innerHTML = data.viewers.map(function(v) {
                    return '<tr><td><b>' + v.name + '</b></td><td>' + v.follow_status + '</td><td>' + v.duration + '</td><td>' + v.total + '回</td>' +
                        '<td><div class="memo-text">' + v.memo + '</div><button class="btn-memo" onclick="openMemoModal(\'' + v.uid + '\', \'' + v.name + '\', \'' + v.memo.replace(/'/g, "\\'") + '\')">📝</button></td>' +
                        '<td style="text-align:right; vertical-align:bottom;"><button class="btn-so" onclick="shoutout(\'' + v.login + '\', \'' + v.name + '\')">📣 SO</button></td></tr>';
                }).join('');
            }
        }
    }).catch(function(e) { console.error(e); });

    fetch('/api/history').then(function(r) { return r.json(); }).then(function(data) {
        var table = document.getElementById('historyTable');
        var tbody = document.getElementById('history-tbody');
        if (!tbody || !table) return;
        var sortIdx = -1, sortDir = null;
        var headers = table.getElementsByTagName("TH");
        for (var i = 0; i < headers.length; i++) {
            if (headers[i].classList.contains("asc")) { sortIdx = i; sortDir = "asc"; break; }
            if (headers[i].classList.contains("desc")) { sortIdx = i; sortDir = "desc"; break; }
        }
        tbody.innerHTML = data.map(function(v) {
            return '<tr><td>' + v.name + ' <br><span style="font-size:0.8em; color:#888;">(' + v.login + ')</span></td>' +
                '<td data-sort="' + v.follow_sort + '">' + (v.is_follower ? '<div class="follow-status">✅</div><div style="font-size:0.75em; color:#888;">' + v.followed_at + '~</div>' : '-') + '</td>' +
                '<td data-sort="' + v.total_visits + '">' + v.total_visits + '回</td><td data-sort="' + v.total_duration_raw + '">' + v.total_duration + '</td>' +
                '<td data-sort="' + v.total_comments + '">' + v.total_comments + '回</td><td data-sort="' + v.total_bits + '" class="' + (v.total_bits ? 'bits-count' : '') + '">' + v.total_bits + '</td>' +
                '<td data-sort="' + (v.is_sub ? 1 : 0) + '" class="' + (v.is_sub ? 'sub-active' : '') + '">' + (v.is_sub ? '⭐' : '-') + '</td>' +
                '<td data-sort="' + v.streak + '">' + (v.streak > 1 ? '🔥' + v.streak : '-') + '</td>' +
                '<td><div class="memo-text">' + v.memo + '</div><button class="btn-memo" onclick="openMemoModal(\'' + v.uid + '\', \'' + v.name + '\', \'' + v.memo_esc + '\')">📝</button></td>' +
                '<td data-sort="' + v.last_seen_sort + '">' + v.last_seen_str + '</td></tr>';
        }).join('');
        if (sortIdx !== -1 && sortDir) {
            sortTable('historyTable', sortIdx, sortDir);
        }
    }).catch(function(e) { console.error(e); });
}, 10000);
