// Analytics page JavaScript
var trendChart = null;
var timelineChart = null;
var currentPage = 1;
var itemsPerPage = 50;
var currentFilteredData = [];
var timelineEndDate = new Date();
var currentYear = new Date().getFullYear();
var currentMonth = new Date().getMonth();
var yAxisRanges = { y: {}, y1: {}, y2: {} };
var monthNames = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"];

// Tab switching
function switchTab(tabId) {
    document.querySelectorAll('.tab-content').forEach(function(el) { el.classList.remove('active'); });
    document.querySelectorAll('.tab-btn').forEach(function(el) { el.classList.remove('active'); });
    document.getElementById(tabId).classList.add('active');
    document.getElementById('btn-' + tabId).classList.add('active');
    if (tabId === 'calendar') {
        renderCalendar(currentYear, currentMonth);
        renderWeeklyTimeline();
    }
    if (tabId === 'trends') initTrendPage();
}

function initTrendPage() {
    if (!trendChart) setRange('1M');
}

function setRange(rangeType) {
    document.querySelectorAll('.range-btn').forEach(function(b) { b.classList.remove('active'); });
    var btn = document.getElementById('btn-range-' + rangeType);
    if (btn) btn.classList.add('active');

    var now = new Date();
    var startDate = new Date();
    var endDate = new Date();

    if (rangeType === '1W') { startDate.setDate(now.getDate() - 7); }
    else if (rangeType === '1M') { startDate.setMonth(now.getMonth() - 1); }
    else if (rangeType === '1Y') { startDate.setFullYear(now.getFullYear() - 1); }
    else if (rangeType === 'ALL') { startDate = allStreams.length > 0 ? new Date(allStreams[allStreams.length - 1].start_time) : new Date(2020, 0, 1); }
    else if (rangeType === 'CUSTOM') {
        var s = document.getElementById('start-date').value;
        var e = document.getElementById('end-date').value;
        if (s) startDate = new Date(s);
        if (e) endDate = new Date(e);
        endDate.setHours(23, 59, 59);
    }

    if (rangeType !== 'CUSTOM') {
        document.getElementById('start-date').value = formatDateForInput(startDate);
        document.getElementById('end-date').value = formatDateForInput(endDate);
    }
    updateTrendChart(startDate, endDate);
}

function updateTrendChart(startDate, endDate) {
    var filtered = allStreams.filter(function(s) {
        var d = new Date(s.start_time);
        return d >= startDate && d <= endDate;
    }).sort(function(a, b) { return new Date(a.start_time) - new Date(b.start_time); });

    renderChart(filtered, startDate, endDate);
    calculateAndShowStats(filtered);
    currentFilteredData = filtered.slice().reverse();
    currentPage = 1;
    renderFilteredList();
}

function calculateAndShowStats(data) {
    var totalAvgViewers = 0, avgCount = 0, totalDurationSec = 0, gameStats = {};

    data.forEach(function(s) {
        if (s.source !== 'api') { totalAvgViewers += (s.avg_viewers || 0); avgCount++; }
        var dur = 0;
        if (s.duration) {
            var parts = s.duration.match(/(\d+)h\s*(\d+)m/) || s.duration.match(/(\d+)m/);
            if (parts) {
                if (parts.length === 3) dur = parseInt(parts[1]) * 3600 + parseInt(parts[2]) * 60;
                else dur = parseInt(parts[1]) * 60;
            }
        }
        totalDurationSec += dur;

        var gName = s.game_name || "Unknown";
        if (!gameStats[gName]) { gameStats[gName] = { count: 0, totalAvg: 0, avgCount: 0, totalDur: 0 }; }
        gameStats[gName].count++;
        gameStats[gName].totalDur += dur;
        if (s.source !== 'api') { gameStats[gName].totalAvg += (s.avg_viewers || 0); gameStats[gName].avgCount++; }
    });

    var overallAvg = avgCount > 0 ? (totalAvgViewers / avgCount).toFixed(1) : "-";
    var h = Math.floor(totalDurationSec / 3600);
    var m = Math.floor((totalDurationSec % 3600) / 60);

    document.getElementById('stat-avg-viewers').innerText = overallAvg;
    document.getElementById('stat-total-time').innerText = h + 'h ' + m + 'm';
    document.getElementById('stat-stream-count').innerText = data.length + "回";

    var tbody = document.getElementById('gamestats-tbody');
    tbody.innerHTML = '';
    var sortedGames = Object.keys(gameStats).sort(function(a, b) {
        var avgA = gameStats[a].avgCount > 0 ? gameStats[a].totalAvg / gameStats[a].avgCount : 0;
        var avgB = gameStats[b].avgCount > 0 ? gameStats[b].totalAvg / gameStats[b].avgCount : 0;
        return avgB - avgA;
    });
    sortedGames.forEach(function(g) {
        var st = gameStats[g];
        var avg = st.avgCount > 0 ? (st.totalAvg / st.avgCount).toFixed(1) : "-";
        var gh = Math.floor(st.totalDur / 3600);
        var gm = Math.floor((st.totalDur % 3600) / 60);
        tbody.innerHTML += '<tr><td>' + g + '</td><td>' + avg + '</td><td>' + gh + 'h ' + gm + 'm</td><td>' + st.count + '</td></tr>';
    });
}

// Search filtering
function filterTable() {
    var input = document.getElementById("searchInput");
    var filter = input.value.toLowerCase();
    var table = document.getElementById("streamTable");
    var tr = table.getElementsByTagName("tr");
    for (var i = 1; i < tr.length; i++) {
        var tds = tr[i].getElementsByTagName("td");
        var txtValue = "";
        for (var j = 0; j < tds.length; j++) { txtValue += tds[j].textContent || tds[j].innerText; }
        tr[i].style.display = txtValue.toLowerCase().indexOf(filter) > -1 ? "" : "none";
    }
}

// Sort stream table
function sortStreamTable(n) {
    var table = document.getElementById("streamTable");
    var rows, switching, i, x, y, shouldSwitch, dir, switchcount = 0;
    switching = true; dir = "asc";
    while (switching) {
        switching = false; rows = table.rows;
        for (i = 1; i < (rows.length - 1); i++) {
            shouldSwitch = false;
            x = rows[i].getElementsByTagName("TD")[n];
            y = rows[i + 1].getElementsByTagName("TD")[n];
            var xVal = x.textContent.toLowerCase();
            var yVal = y.textContent.toLowerCase();
            if (dir == "asc") { if (xVal > yVal) { shouldSwitch = true; break; } }
            else if (dir == "desc") { if (xVal < yVal) { shouldSwitch = true; break; } }
        }
        if (shouldSwitch) {
            rows[i].parentNode.insertBefore(rows[i + 1], rows[i]);
            switching = true; switchcount++;
        } else {
            if (switchcount == 0 && dir == "asc") { dir = "desc"; switching = true; }
        }
    }
}

// Simple list renderer
function renderSimpleList(data, elementId) {
    var tbody = document.getElementById(elementId);
    if (!tbody) return;
    var sorted = data.slice().sort(function(a, b) { return new Date(b.start_time) - new Date(a.start_time); });
    if (sorted.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:#999; padding:10px;">データなし</td></tr>';
        return;
    }
    tbody.innerHTML = sorted.map(function(s) {
        return '<tr><td style="width:120px;">' + s.start_time.substring(5, 16).replace('T', ' ') + '</td>' +
            '<td><a href="/analytics/stream/' + s.sid + '" target="_blank" style="font-weight:bold; color:#6441a5; text-decoration:none;">' + s.title + '</a></td>' +
            '<td>' + s.game_name + '</td><td style="width:80px;">' + (s.duration_short || '-') + '</td></tr>';
    }).join('');
}

// Set timeline start date from calendar click
function setTimelineStartDate(dateStr) {
    var d = new Date(dateStr);
    d.setDate(d.getDate() + 6);
    timelineEndDate = d;
    var picker = document.getElementById('timelinePicker');
    if (picker) {
        picker.value = d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
    }
    renderWeeklyTimeline();
    renderCalendar(currentYear, currentMonth);
}

// Calendar
function changeMonth(d) {
    currentMonth += d;
    if (currentMonth < 0) { currentMonth = 11; currentYear--; }
    else if (currentMonth > 11) { currentMonth = 0; currentYear++; }
    renderCalendar(currentYear, currentMonth);
}

function renderCalendar(year, month) {
    var grid = document.getElementById('calendarGrid');
    var title = document.getElementById('calendarTitle');
    grid.innerHTML = '<div class="cal-day-header">日</div><div class="cal-day-header">月</div><div class="cal-day-header">火</div><div class="cal-day-header">水</div><div class="cal-day-header">木</div><div class="cal-day-header">金</div><div class="cal-day-header">土</div>';
    title.innerText = year + '年 ' + monthNames[month];

    var firstDay = new Date(year, month, 1).getDay();
    var daysInMonth = new Date(year, month + 1, 0).getDate();
    var monthStart = new Date(year, month, 1);
    var monthEnd = new Date(year, month + 1, 0, 23, 59, 59);

    // タイムラインの現在範囲
    var tlEnd = new Date(timelineEndDate);
    var tlStart = new Date(timelineEndDate);
    tlStart.setDate(tlStart.getDate() - 6);
    var tlStartStr = formatDateForInput(tlStart);
    var tlEndStr = formatDateForInput(tlEnd);

    var monthlyData = allStreams.filter(function(s) {
        var d = new Date(s.start_time);
        return d >= new Date(monthStart.getTime() - 86400000) && d <= new Date(monthEnd.getTime() + 86400000);
    });
    renderSimpleList(monthlyData, 'monthly-list-tbody');

    for (var i = 0; i < firstDay; i++) { grid.innerHTML += '<div class="cal-day other-month"></div>'; }

    for (var day = 1; day <= daysInMonth; day++) {
        var dateStr = year + '-' + String(month + 1).padStart(2, '0') + '-' + String(day).padStart(2, '0');
        var eventsHtml = '';
        var inTimeline = dateStr >= tlStartStr && dateStr <= tlEndStr;
        var tlClass = inTimeline ? ' in-timeline' : '';

        var daysStreams = monthlyData.filter(function(s) {
            var utcDate = new Date(s.start_time);
            var jstDate = new Date(utcDate.getTime() + 9 * 60 * 60 * 1000);
            var sDateStr = jstDate.toISOString().substring(0, 10);
            return sDateStr === dateStr;
        });

        daysStreams.forEach(function(s) {
            var utcDate = new Date(s.start_time);
            var jstDate = new Date(utcDate.getTime() + 9 * 60 * 60 * 1000);
            var timeStr = jstDate.toISOString().substring(11, 16);
            eventsHtml += '<div class="cal-event" onclick="event.stopPropagation(); window.location.href=\'/analytics/stream/' + s.sid + '\'" title="' + s.title + '">🎥 ' + timeStr + ' ' + s.game_name + '</div>';
        });
        grid.innerHTML += '<div class="cal-day' + tlClass + '" onclick="setTimelineStartDate(\'' + dateStr + '\')"><div class="cal-date-num">' + day + '</div>' + eventsHtml + '</div>';
    }
}

// Weekly timeline
function changeTimelineDate(val) {
    if (val) { timelineEndDate = new Date(val); renderWeeklyTimeline(); renderCalendar(currentYear, currentMonth); }
}

function moveTimeline(days) {
    if (days === 0) { timelineEndDate = new Date(); }
    else { timelineEndDate.setDate(timelineEndDate.getDate() + days); }
    var picker = document.getElementById('timelinePicker');
    if (picker) {
        var y = timelineEndDate.getFullYear();
        var m = String(timelineEndDate.getMonth() + 1).padStart(2, '0');
        var d = String(timelineEndDate.getDate()).padStart(2, '0');
        picker.value = y + '-' + m + '-' + d;
    }
    renderWeeklyTimeline();
    renderCalendar(currentYear, currentMonth);
}

function renderWeeklyTimeline() {
    var ctx = document.getElementById('weeklyTimelineCanvas').getContext('2d');
    var endDate = new Date(timelineEndDate);
    endDate.setHours(23, 59, 59, 999);
    var startDate = new Date(timelineEndDate);
    startDate.setDate(startDate.getDate() - 6);
    startDate.setHours(0, 0, 0, 0);

    var weeklyData = allStreams.filter(function(s) {
        var d = new Date(s.start_time);
        return d >= startDate && d <= endDate;
    });
    renderSimpleList(weeklyData, 'weekly-list-tbody');

    var labels = [];
    for (var i = 0; i < 7; i++) {
        var d = new Date(startDate);
        d.setDate(d.getDate() + i);
        labels.push((d.getMonth() + 1) + '/' + d.getDate());
    }

    var datasets = [{ label: '配信時間帯', data: [], backgroundColor: 'rgba(100, 65, 165, 0.6)', borderColor: 'rgba(100, 65, 165, 1)', borderWidth: 1, barPercentage: 0.6 }];

    weeklyData.forEach(function(s) {
        var d = new Date(s.start_time);
        var dateStr = (d.getMonth() + 1) + '/' + d.getDate();
        var startH = d.getHours() + d.getMinutes() / 60;
        var endH = startH;
        if (s.duration) {
            var parts = s.duration.match(/(\d+)h\s*(\d+)m/) || s.duration.match(/(\d+)m/);
            if (parts) {
                var durM = 0;
                if (parts.length === 3) durM = parseInt(parts[1]) * 60 + parseInt(parts[2]);
                else durM = parseInt(parts[1]);
                endH += durM / 60;
            }
        }
        if (endH > 24) {
            if (labels.includes(dateStr)) { datasets[0].data.push({ x: [startH, 24], y: dateStr, title: s.title }); }
            var nextDay = new Date(d);
            nextDay.setDate(nextDay.getDate() + 1);
            var nextDateStr = (nextDay.getMonth() + 1) + '/' + nextDay.getDate();
            if (labels.includes(nextDateStr)) { datasets[0].data.push({ x: [0, endH - 24], y: nextDateStr, title: s.title + " (続き)" }); }
        } else {
            if (labels.includes(dateStr)) { datasets[0].data.push({ x: [startH, endH], y: dateStr, title: s.title }); }
        }
    });

    if (timelineChart) { timelineChart.destroy(); }
    timelineChart = new Chart(ctx, {
        type: 'bar',
        data: { labels: labels, datasets: datasets },
        options: {
            indexAxis: 'y', animation: false, responsive: true, maintainAspectRatio: false,
            scales: {
                x: { min: 0, max: 24, title: { display: true, text: '時間 (0-24)' }, ticks: { stepSize: 1 } },
                y: { title: { display: true, text: '日付' } }
            },
            plugins: { tooltip: { callbacks: { label: function(ctx) { return ctx.raw.title || ''; } } } }
        }
    });
}

// Helper: format Date for input[type=date]
function formatDateForInput(d) {
    return d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
}

// Zoom/Pan callback: sync summary, list, and date pickers with visible chart range
function onChartRangeChange(ctx) {
    var xScale = ctx.chart.scales.x;
    var visibleMin = new Date(xScale.min);
    var visibleMax = new Date(xScale.max);
    var filtered = allStreams.filter(function(s) {
        var d = new Date(s.start_time);
        return d >= visibleMin && d <= visibleMax;
    }).sort(function(a, b) { return new Date(a.start_time) - new Date(b.start_time); });
    calculateAndShowStats(filtered);
    currentFilteredData = filtered.slice().reverse();
    currentPage = 1;
    renderFilteredList();
    document.getElementById('start-date').value = formatDateForInput(visibleMin);
    document.getElementById('end-date').value = formatDateForInput(visibleMax);
}

// Sync y-axis range inputs with yAxisRanges state
function syncYAxisInputs() {
    ['y', 'y1', 'y2'].forEach(function(axisId) {
        var minEl = document.getElementById(axisId + '-min');
        var maxEl = document.getElementById(axisId + '-max');
        if (minEl) minEl.value = yAxisRanges[axisId].min !== undefined ? yAxisRanges[axisId].min : '';
        if (maxEl) maxEl.value = yAxisRanges[axisId].max !== undefined ? yAxisRanges[axisId].max : '';
    });
}

// Update a single y-axis range from input, persist across redraws
function updateYAxis(axisId, field, value) {
    var val = value === '' ? undefined : parseFloat(value);
    if (!isNaN(val) || val === undefined) {
        yAxisRanges[axisId][field] = val;
    }
    if (trendChart) {
        trendChart.options.scales[axisId][field] = val;
        trendChart.update('none');
    }
}

// Adjust y-axis ranges to currently visible x range
function adjustYAxisToVisible() {
    if (!trendChart) return;
    var xScale = trendChart.scales.x;
    var visMin = new Date(xScale.min);
    var visMax = new Date(xScale.max);

    var visStreams = allStreams.filter(function(s) {
        var d = new Date(s.start_time);
        return d >= visMin && d <= visMax;
    });
    var visFollowers = followerHistory.filter(function(f) {
        var d = new Date(f.x);
        return d >= visMin && d <= visMax;
    });

    if (visStreams.length > 0) {
        var maxV = Math.max.apply(null, visStreams.map(function(s) { return s.max_viewers || 0; }));
        var durVals = visStreams.map(function(s) {
            if (!s.duration) return 0;
            var parts = s.duration.match(/(\d+)h\s*(\d+)m/) || s.duration.match(/(\d+)m/);
            if (!parts) return 0;
            return parts.length === 3 ? parseInt(parts[1]) * 60 + parseInt(parts[2]) : parseInt(parts[1]);
        });
        var maxD = Math.max.apply(null, durVals);
        yAxisRanges.y = { min: 0, max: maxV || 10 };
        yAxisRanges.y2 = { min: 0, max: maxD || 60 };
    }
    if (visFollowers.length > 0) {
        var fVals = visFollowers.map(function(f) { return f.y || 0; });
        yAxisRanges.y1 = { min: Math.max(0, Math.min.apply(null, fVals)), max: Math.max.apply(null, fVals) || 100 };
    }

    ['y', 'y1', 'y2'].forEach(function(axisId) {
        trendChart.options.scales[axisId].min = yAxisRanges[axisId].min;
        trendChart.options.scales[axisId].max = yAxisRanges[axisId].max;
    });
    trendChart.update('none');
    syncYAxisInputs();
}

// Reset y-axis ranges to auto-calculated values and redraw
function resetYAxisRanges() {
    yAxisRanges = { y: {}, y1: {}, y2: {} };
    var s = document.getElementById('start-date').value;
    var e = document.getElementById('end-date').value;
    if (s && e) {
        var sd = new Date(s);
        var ed = new Date(e);
        ed.setHours(23, 59, 59);
        updateTrendChart(sd, ed);
    }
}

// Trend chart
function renderChart(data, minDate, maxDate) {
    var ctx = document.getElementById('trendChartCanvas').getContext('2d');
    var pointsViewers = allStreams.map(function(s) { return { x: s.start_time, y: s.max_viewers, title: s.title, game: s.game_name }; });
    var pointsDurations = allStreams.map(function(s) {
        if (!s.duration || typeof s.duration !== 'string') return { x: s.start_time, y: 0 };
        var parts = s.duration.match(/(\d+)h\s*(\d+)m/) || s.duration.match(/(\d+)m/);
        var val = 0;
        if (parts) {
            if (parts.length === 3) val = parseInt(parts[1]) * 60 + parseInt(parts[2]);
            else val = parseInt(parts[1]);
        }
        return { x: s.start_time, y: val };
    });

    // Initialize y-axis ranges from all data on first render
    if (yAxisRanges.y.max === undefined) {
        var maxV = Math.max.apply(null, allStreams.map(function(s) { return s.max_viewers || 0; }).concat([0]));
        yAxisRanges.y = { min: 0, max: maxV || 100 };
    }
    if (yAxisRanges.y1.max === undefined && followerHistory && followerHistory.length > 0) {
        var fVals = followerHistory.map(function(f) { return f.y || 0; });
        var minF = Math.min.apply(null, fVals);
        var maxF = Math.max.apply(null, fVals);
        yAxisRanges.y1 = { min: Math.max(0, minF), max: maxF || 100 };
    }
    if (yAxisRanges.y2.max === undefined) {
        var maxD = Math.max.apply(null, pointsDurations.map(function(p) { return p.y || 0; }).concat([0]));
        yAxisRanges.y2 = { min: 0, max: maxD || 300 };
    }

    if (trendChart) { trendChart.destroy(); }
    trendChart = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [
                { label: '最大同接数', data: pointsViewers, borderColor: '#6441a5', backgroundColor: '#6441a5', yAxisID: 'y', tension: 0.1 },
                { label: 'フォロワー数', data: followerHistory, borderColor: '#e91e63', backgroundColor: '#e91e63', yAxisID: 'y1', tension: 0.1, pointRadius: 0, borderWidth: 2 },
                { label: '配信時間(分)', data: pointsDurations, borderColor: '#009688', backgroundColor: 'rgba(0,150,136,0.1)', fill: true, tension: 0.1, yAxisID: 'y2' }
            ]
        },
        options: {
            responsive: true, maintainAspectRatio: false, animation: false,
            scales: {
                x: { type: 'time', time: { unit: 'day', tooltipFormat: 'yyyy/MM/dd HH:mm' }, min: minDate.toISOString(), max: maxDate.toISOString(), title: { display: true, text: '日付' } },
                y:  { type: 'linear', display: true,  position: 'left',  min: yAxisRanges.y.min,  max: yAxisRanges.y.max,  title: { display: true, text: '同接 (人)' } },
                y1: { type: 'linear', display: true,  position: 'right', min: yAxisRanges.y1.min, max: yAxisRanges.y1.max, grid: { drawOnChartArea: false }, title: { display: true, text: 'フォロワー (人)' } },
                y2: { type: 'linear', display: false, position: 'right', min: yAxisRanges.y2.min, max: yAxisRanges.y2.max, grid: { drawOnChartArea: false } }
            },
            plugins: {
                tooltip: {
                    callbacks: {
                        afterLabel: function(context) {
                            var raw = context.raw;
                            if (raw.game || raw.title) { return ['🎮 ' + raw.game, '📝 ' + raw.title]; }
                            return [];
                        }
                    }
                },
                zoom: {
                    pan: {
                        enabled: true,
                        mode: 'x',
                        modifierKey: null,
                        onPanComplete: onChartRangeChange
                    },
                    zoom: {
                        wheel: { enabled: true },
                        pinch: { enabled: true },
                        mode: 'x',
                        onZoomComplete: onChartRangeChange
                    }
                }
            }
        }
    });
    syncYAxisInputs();
}

// Pagination
function changePage(delta) {
    currentPage += delta;
    renderFilteredList();
}

function renderFilteredList() {
    var tbody = document.getElementById('trend-list-tbody');
    var pageInfo = document.getElementById('page-info');
    if (!tbody) return;

    var totalItems = currentFilteredData.length;
    var totalPages = Math.ceil(totalItems / itemsPerPage);
    if (currentPage < 1) currentPage = 1;
    if (currentPage > totalPages && totalPages > 0) currentPage = totalPages;

    var start = (currentPage - 1) * itemsPerPage;
    var end = start + itemsPerPage;
    var pageData = currentFilteredData.slice(start, end);

    if (pageData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:20px; color:#999;">表示範囲内に配信データがありません</td></tr>';
        pageInfo.innerText = "0 / 0 ページ";
        return;
    }
    pageInfo.innerText = currentPage + ' / ' + totalPages + ' ページ (' + totalItems + '件)';

    tbody.innerHTML = pageData.map(function(s) {
        return '<tr><td><div style="font-weight:bold;">' + s.start_time.substring(0, 10) + '</div><div style="font-size:0.8em; color:#888;">' + s.start_time.substring(11, 16) + '</div></td>' +
            '<td><a href="/analytics/stream/' + s.sid + '" target="_blank" style="font-weight:bold; color:#6441a5; text-decoration:none;">' + s.title + '</a></td>' +
            '<td>' + s.game_name + '</td><td><b>' + s.max_viewers + '</b> / ' + s.avg_viewers + '</td>' +
            '<td>' + (s.follower_count ? s.follower_count + '人' : '<span style="color:#ccc;">-</span>') + '</td></tr>';
    }).join('');
}

// Download progress polling
setInterval(function() {
    fetch('/api/download_progress').then(function(r) { return r.json(); }).then(function(data) {
        var activeSids = new Set(Object.keys(data));
        var shouldReload = false;
        for (var sid in data) {
            var info = data[sid];
            var el = document.getElementById('progress-' + sid);
            if (el) {
                if (info.status === 'downloading') {
                    el.innerHTML = '<div class="status-wait">📥 ' + info.percent + '%</div><div style="font-size:0.7em; color:#666;">(' + info.speed + ')</div>';
                } else if (info.status === 'finished') {
                    el.innerHTML = '<div class="status-wait">🔄 仕上げ中...</div>';
                } else if (info.status === 'failed') {
                    el.innerHTML = '<span class="status-fail">❌ エラー</span>';
                }
            }
        }
        document.querySelectorAll('[id^="progress-"]').forEach(function(el) {
            if (el.querySelector('.status-wait')) {
                var sid = el.id.replace('progress-', '');
                if (!activeSids.has(sid)) { shouldReload = true; }
            }
        });
        if (shouldReload) { location.reload(); }
    });
}, 2000);

// Edit stream modal
function openEditStreamModal(sid, title, game) {
    document.getElementById('edit_stream_id').value = sid;
    document.getElementById('edit_stream_title').value = title;
    document.getElementById('edit_stream_game').value = game;
    document.getElementById('editStreamModal').style.display = 'block';
}
function closeEditStreamModal() {
    document.getElementById('editStreamModal').style.display = 'none';
}

// 初期化: タイムライン日付ピッカーにtodayをセット
(function() {
    var picker = document.getElementById('timelinePicker');
    if (picker) {
        var d = timelineEndDate;
        picker.value = d.getFullYear() + '-' + String(d.getMonth()+1).padStart(2,'0') + '-' + String(d.getDate()).padStart(2,'0');
    }
})();
