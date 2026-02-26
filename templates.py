# templates.py

# --- メイン画面テンプレート ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Iniwa`s Twitch BOT</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { font-family: "Helvetica Neue", Arial, sans-serif; margin: 0; padding: 20px; background: #f4f6f9; color: #333; }
        
        .dashboard-grid { 
            display: grid; 
            grid-template-columns: repeat({{ config.layout.columns }}, 1fr); 
            gap: 20px; 
            max-width: {{ config.layout.max_width }}px; 
            margin: 0 auto; 
            align-items: stretch;
        }
        
        @media (max-width: 900px) { 
            .dashboard-grid { grid-template-columns: 1fr !important; } 
            .card { grid-column: span 1 !important; }
        }

        .card { 
            background: white; padding: 20px; border-radius: 12px; 
            box-shadow: 0 2px 5px rgba(0,0,0,0.05); 
            display: flex; flex-direction: column; 
            overflow: hidden; 
            height: 100%;
            box-sizing: border-box;
        }
        
        .card-scroll-area {
            flex: 1;
            overflow-y: auto;
            min-height: 0;
            margin-bottom: 10px;
        }

        h2 { margin-top: 0; color: #2c3e50; border-bottom: 2px solid #f0f0f0; padding-bottom: 10px; font-size: 1.1em; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; flex-shrink: 0; }
        
        form, .control-group, .rule-footer, .filter-bar { flex-shrink: 0; }

        .btn { padding: 6px 12px; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; color: white; text-decoration: none; display: inline-block; font-size: 0.9em; transition: 0.2s; white-space: nowrap; }
        .btn:hover { opacity: 0.8; }
        .btn-success { background: #2ecc71; } .btn-stop { background: #e74c3c; } .btn-primary { background: #6441a5; } 
        .btn-secondary { background: #6c757d; } .btn-info { background: #17a2b8; } .btn-danger { background: #d73a49; }
        .btn-apply { background: #f39c12; } .btn-edit { background: #007bff; padding: 4px 8px; font-size: 0.8em; }
        .btn-move { background: #6c757d; padding: 4px 8px; font-size: 0.8em; }
        .btn-so { background: #9c27b0; color: white; padding: 2px 8px; font-size: 0.75em; border-radius: 4px; border:none; cursor:pointer; }
        .btn-memo { background: #ff9800; color: white; padding: 2px 6px; font-size: 0.75em; border-radius: 4px; border:none; cursor:pointer; margin-left:5px;}
        .btn-analytics { background: #ff5722; color: white; }
        .btn-purple { background: #673ab7; color: white; } 
        /* Twitter(X)ボタン用のスタイル */
        .btn-x { background: #000; color: white; padding: 4px 12px; font-size: 0.85em; display: flex; align-items: center; gap: 5px; }

        .status-badge { padding: 4px 10px; border-radius: 20px; font-weight: bold; font-size: 0.85em; display: inline-flex; align-items: center; gap: 5px; }
        .running { background: #d4edda; color: #155724; } .stopped { background: #f8d7da; color: #721c24; }
        table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 0.9em; }
        th, td { padding: 8px; text-align: left; border-bottom: 1px solid #eee; }
        th { background-color: #f8f9fa; position: sticky; top: 0; cursor: pointer; user-select: none; }
        th.asc::after { content: ' ▲'; color: #6441a5; } th.desc::after { content: ' ▼'; color: #6441a5; }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; margin-bottom: 5px; font-weight: bold; font-size: 0.9em; color: #555; }
        .form-group input[type="text"], .form-group input[type="password"], .form-group input[type="number"] { width: 100%; padding: 8px; box-sizing: border-box; border: 1px solid #ddd; border-radius: 4px; }
        .form-group textarea { width: 100%; padding: 8px; box-sizing: border-box; border: 1px solid #ddd; border-radius: 4px; }
        .logs { background: #1e1e1e; color: #00ff00; padding: 10px; font-family: monospace; font-size: 0.8em; border-radius: 6px; }
        .rule-card { background:#fafbfc; border:1px solid #ddd; padding:10px; border-radius:8px; margin-bottom:10px; }
        .rule-header { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; flex-wrap: wrap;}
        .rule-body { margin-bottom: 8px; }
        .rule-footer { display: flex; justify-content: space-between; align-items: center; }
        .modal { display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.5); }
        #memoModal { z-index: 2000; }
        .modal-content { background-color: white; margin: 2vh auto; padding: 25px; border-radius: 10px; width: 90%; max-width: 1000px; max-height: 96vh; overflow-y: auto; position: relative; box-shadow: 0 4px 15px rgba(0,0,0,0.2); }
        .close { float: right; font-size: 24px; font-weight: bold; cursor: pointer; color: #aaa; }
        .filter-bar { display: flex; flex-wrap: wrap; gap: 5px; margin-bottom: 15px; padding-bottom: 10px; border-bottom: 1px solid #eee; }
        .filter-btn { padding: 5px 12px; border: 1px solid #ddd; background: #fff; border-radius: 15px; cursor: pointer; font-size: 0.85em; color: #555; transition: 0.2s; }
        .filter-btn:hover { background: #f0f0f0; }
        .filter-btn.active { background: #6441a5; color: white; border-color: #6441a5; }
        .memo-text { font-size:0.8em; color:#666; display:block; max-width:150px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .follow-status { font-size:0.8em; color:#2e7d32; font-weight:bold; }
        .unfollow-status { font-size:0.8em; color:#c62828; font-weight:bold; }
        .watching-status { color: #2ecc71; font-weight: bold; }
        .bits-count { color: #e91e63; font-weight: bold; }
        .sub-active { color: #9c27b0; font-weight: bold; }
        .layout-table { width: 100%; font-size: 0.9em; margin-top: 10px; }
        .layout-table th, .layout-table td { padding: 8px; border-bottom: 1px solid #eee; text-align: left; }
        .layout-table input { width: 80px; padding: 4px; }
        .tag-badge { 
            background: #e0e0e0; color: #333; 
            padding: 2px 6px; border-radius: 4px; 
            font-size: 0.75em; margin-right: 4px; display: inline-block; 
        }
    </style>
    <script>
        function toggleModal(id) { const modal = document.getElementById(id); modal.style.display = (modal.style.display === "block") ? "none" : "block"; }
        
        function openMemoModal(uid, name, currentMemo) {
            document.getElementById('memo_user_id').value = uid;
            document.getElementById('memo_user_name').innerText = name;
            document.getElementById('memo_text').value = currentMemo;
            toggleModal('memoModal');
        }
        function filterRules(gameName, btnElement) {
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            if(btnElement) btnElement.classList.add('active');
            const cards = document.querySelectorAll('.rule-card');
            cards.forEach(card => {
                const cardGame = card.getAttribute('data-game');
                if (gameName === '__ALL__' || cardGame === gameName) { card.style.display = 'block'; } else { card.style.display = 'none'; }
            });
        }
        function shoutout(loginName, displayName) {
            if(!confirm(displayName + ' (' + loginName + ') さんをシャウトアウトしますか？')) return;
            const form = document.createElement('form');
            form.method = 'POST'; form.action = '/shoutout';
            const input = document.createElement('input'); input.type = 'hidden'; input.name = 'target_name'; input.value = loginName;
            form.appendChild(input); document.body.appendChild(form); form.submit();
        }
        function sortTable(tableId, n, forceDir) { 
            var table, rows, switching, i, x, y, shouldSwitch, dir, switchcount = 0;
            table = document.getElementById(tableId); var headers = table.getElementsByTagName("TH"); var currentHeader = headers[n];
            if (forceDir) { dir = forceDir; } 
            else { var isAsc = currentHeader.classList.contains('asc'); dir = isAsc ? "desc" : "asc"; }
            for (var h of headers) { h.classList.remove('asc', 'desc'); } currentHeader.classList.add(dir);
            switching = true;
            while (switching) {
                switching = false; rows = table.getElementsByTagName("TR");
                for (i = 1; i < (rows.length - 1); i++) {
                    shouldSwitch = false; x = rows[i].getElementsByTagName("TD")[n]; y = rows[i + 1].getElementsByTagName("TD")[n];
                    var xVal = x.getAttribute("data-sort") ? parseFloat(x.getAttribute("data-sort")) : x.innerHTML.toLowerCase();
                    var yVal = y.getAttribute("data-sort") ? parseFloat(y.getAttribute("data-sort")) : y.innerHTML.toLowerCase();
                    if (dir == "asc") { if (xVal > yVal) { shouldSwitch = true; break; } } 
                    else if (dir == "desc") { if (xVal < yVal) { shouldSwitch = true; break; } }
                }
                if (shouldSwitch) { rows[i].parentNode.insertBefore(rows[i + 1], rows[i]); switching = true; switchcount ++; }
            }
        }
        function fetchCurrentSettings() {
            fetch('/api/current_settings')
                .then(r => r.json())
                .then(data => {
                    if(data.status === 'success') {
                        const form = document.getElementById('addPresetForm');
                        if(form) {
                            form.querySelector('input[name="game"]').value = data.game_name;
                            form.querySelector('input[name="title"]').value = data.title;
                            const nameInput = form.querySelector('input[name="name"]');
                            if(!nameInput.value) { nameInput.value = "現在の設定"; }
                        }
                    } else {
                        alert('情報の取得に失敗しました。');
                    }
                })
                .catch(e => alert('エラーが発生しました: ' + e));
        }

        
        
        setInterval(function() {
            fetch('/api/status').then(r => r.json()).then(data => {
                if(document.getElementById('log-container')) document.getElementById('log-container').innerHTML = data.logs.map(l => `<div>${l}</div>`).join('');
                if(document.getElementById('viewer-count-span')) document.getElementById('viewer-count-span').innerText = `(${data.count}人)`;
                const tb = document.getElementById('viewer-tbody');
                if(tb) {
                    if (data.viewers.length === 0) tb.innerHTML = '<tr><td colspan="6" style="text-align:center; color:#999;">現在なし / オフライン</td></tr>';
                    else tb.innerHTML = data.viewers.map(v => 
                        `<tr><td><b>${v.name}</b></td><td>${v.follow_status}</td><td>${v.duration}</td><td>${v.total}回</td>
                        <td><div class="memo-text">${v.memo}</div><button class="btn-memo" onclick="openMemoModal('${v.uid}', '${v.name}', '${v.memo.replace(/'/g, "\\'")}')">📝</button></td>
                        <td style="text-align:right; vertical-align:bottom;"><button class="btn-so" onclick="shoutout('${v.login}', '${v.name}')">📣 SO</button></td></tr>`
                    ).join('');
                }
            }).catch(e => console.error(e));

            fetch('/api/history').then(r => r.json()).then(data => {
                const table = document.getElementById('historyTable'); const tbody = document.getElementById('history-tbody');
                if(!tbody || !table) return;
                let sortIdx = -1, sortDir = null; const headers = table.getElementsByTagName("TH");
                for(let i=0; i<headers.length; i++) {
                    if(headers[i].classList.contains("asc")) { sortIdx=i; sortDir="asc"; break; }
                    if(headers[i].classList.contains("desc")) { sortIdx=i; sortDir="desc"; break; }
                }
                tbody.innerHTML = data.map(v => `
                    <tr><td>${v.name} <br><span style="font-size:0.8em; color:#888;">(${v.login})</span></td>
                    <td data-sort="${v.follow_sort}">${v.is_follower ? `<div class="follow-status">✅</div><div style="font-size:0.75em; color:#888;">${v.followed_at}~</div>` : '-'}</td>
                    <td data-sort="${v.total_visits}">${v.total_visits}回</td><td data-sort="${v.total_duration_raw}">${v.total_duration}</td>
                    <td data-sort="${v.total_comments}">${v.total_comments}回</td><td data-sort="${v.total_bits}" class="${v.total_bits ? 'bits-count' : ''}">${v.total_bits}</td>
                    <td data-sort="${v.is_sub ? 1 : 0}" class="${v.is_sub ? 'sub-active' : ''}">${v.is_sub ? '⭐' : '-'}</td>
                    <td data-sort="${v.streak}">${v.streak > 1 ? '🔥'+v.streak : '-'}</td>
                    <td><div class="memo-text">${v.memo}</div><button class="btn-memo" onclick="openMemoModal('${v.uid}', '${v.name}', '${v.memo_esc}')">📝</button></td>
                    <td data-sort="${v.last_seen_sort}">${v.last_seen_str}</td></tr>
                `).join('');
                if(sortIdx !== -1 && sortDir) { sortTable('historyTable', sortIdx, sortDir); }
            }).catch(e => console.error(e));
        }, 10000);

        function addOutcomeInput(containerId, value="") {
            const container = document.getElementById(containerId);
            const count = container.querySelectorAll('input').length;
            if (count >= 10) return; // Twitch上限

            const div = document.createElement('div');
            div.style.display = 'flex';
            div.style.gap = '5px';
            div.style.marginBottom = '5px';
            
            const input = document.createElement('input');
            input.type = 'text';
            input.name = 'outcomes';
            input.placeholder = '選択肢 ' + (count + 1);
            input.value = value;
            input.style.flex = '1';
            input.required = true;
            
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.innerText = '✕';
            btn.className = 'btn btn-danger';
            btn.style.padding = '0 8px';
            btn.onclick = function() { container.removeChild(div); };
            
            div.appendChild(input);
            div.appendChild(btn);
            container.appendChild(div);
        }

        function openEditPredictionModal(index, title, duration, outcomesJson) {
            document.getElementById('edit_pred_index').value = index;
            document.getElementById('edit_pred_title').value = title;
            document.getElementById('edit_pred_duration').value = duration;
            
            const container = document.getElementById('edit_pred_outcomes_container');
            container.innerHTML = ''; // クリア
            const outcomes = JSON.parse(outcomesJson);
            outcomes.forEach(o => addOutcomeInput('edit_pred_outcomes_container', o));
            
            toggleModal('editPredictionModal');
        }
        
        // ★修正: tweet_tags引数を追加
        function openEditPreset(index, name, game, title, tags, tweetTags) {
            document.getElementById('edit_preset_index').value = index;
            document.getElementById('edit_preset_name').value = name;
            document.getElementById('edit_preset_game').value = game;
            document.getElementById('edit_preset_title').value = title;
            document.getElementById('edit_preset_tags').value = tags;
            document.getElementById('edit_preset_tweet_tags').value = tweetTags; // セット
            toggleModal('editPresetModal');
        }

        function fetchCurrentSettings() {
            fetch('/api/current_settings')
                .then(r => r.json())
                .then(data => {
                    if(data.status === 'success') {
                        const form = document.getElementById('addPresetForm');
                        if(form) {
                            form.querySelector('input[name="game"]').value = data.game_name;
                            form.querySelector('input[name="title"]').value = data.title;
                            if(data.tags && Array.isArray(data.tags)) {
                                form.querySelector('input[name="tags"]').value = data.tags.join(', ');
                            }
                            const nameInput = form.querySelector('input[name="name"]');
                            if(!nameInput.value) { nameInput.value = "現在の設定"; }
                        }
                    } else {
                        alert('情報の取得に失敗しました。');
                    }
                })
                .catch(e => alert('エラーが発生しました: ' + e));
        }

        // --- ★修正: 配信開始ツイート (指定タグ対応) ---
        function tweetStream() {
            fetch('/api/current_settings')
                .then(r => r.json())
                .then(data => {
                    if(data.status === 'success') {
                        // 1. チャンネルURL
                        const url = `https://twitch.tv/{{ config.channel_name }}`;
                        
                        // 2. ツイート用タグの生成
                        // 優先順位: プリセット設定タグ > 自動生成タグ
                        let tags = "";
                        
                        if (data.tweet_tags && data.tweet_tags.trim() !== "") {
                            // 設定されている場合、それを使用
                            tags = data.tweet_tags;
                        } else {
                            // 未設定の場合、自動生成 (ゲーム名 + デフォルト)
                            const gameTag = data.game_name ? '#' + data.game_name.replace(/\s+/g, '') : '';
                            tags = `${gameTag} #Twitch #配信`;
                        }

                        // 3. ツイート本文の構築
                        // 指定フォーマット:
                        // 配信開始しました！(改行)
                        // 配信タイトル(改行)
                        // タグ(改行)
                        // TwitchURL
                        const text = `配信開始しました！\\n${data.title}\\n${tags}\\n`;
                        
                        // 4. URL生成とオープン
                        const tweetUrl = `https://x.com/intent/tweet?text=${encodeURIComponent(text)}&url=${encodeURIComponent(url)}`;
                        window.open(tweetUrl, '_blank');
                    } else {
                        alert('配信情報の取得に失敗しました。');
                    }
                })
                .catch(e => alert('エラーが発生しました: ' + e));
        }
    </script>
</head>
<body>
    <datalist id="existing_games"><option value="All"><option value="Default">{% for g in unique_games %}<option value="{{ g }}">{% endfor %}</datalist>
    <div class="dashboard-grid">
        <div class="card" style="order: {{ config.layout.cards.viewers.order }}; grid-column: span {{ config.layout.cards.viewers.span }}; {% if config.layout.cards.viewers.height > 0 %}height: {{ config.layout.cards.viewers.height }}px;{% endif %}">
            <h2><div>📊 視聴者リスト <span id="viewer-count-span" style="font-size:0.8em; color:#666;">({{ active_viewers|length }}人)</span></div>
                <div style="display:flex; gap:5px;"><a href="/analytics" class="btn btn-analytics">📈 アナリティクス</a><button class="btn btn-purple" onclick="toggleModal('followerModal')">👥 フォロワー</button><button class="btn btn-info" onclick="toggleModal('historyModal')">📜 履歴</button></div></h2>
            
            <div class="card-scroll-area">
                <table>
                    <thead><tr><th>名前</th><th>フォロー</th><th>滞在</th><th>回数</th><th>メモ</th><th style="width:80px;">Action</th></tr></thead>
                    <tbody id="viewer-tbody">
                        {% for viewer in active_viewers %}
                        <tr>
                            <td><b>{{ viewer.name }}</b></td>
                            <td>{{ viewer.follow_status|safe }}</td>
                            <td>{{ viewer.duration }}</td>
                            <td>{{ viewer.total }}回</td>
                            <td>
                                <div class="memo-text">{{ viewer.memo }}</div>
                                <button class="btn-memo" onclick="openMemoModal('{{ viewer.uid }}', '{{ viewer.name }}', '{{ viewer.memo|replace("'", "\\'") }}')">📝</button>
                            </td>
                            <td style="text-align:right; vertical-align:bottom;">
                                <button class="btn-so" onclick="shoutout('{{ viewer.login }}', '{{ viewer.name }}')">📣 SO</button>
                            </td>
                        </tr>
                        {% else %}
                        <tr><td colspan="6" style="text-align:center; color:#999;">現在なし / オフライン</td></tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
            
            <form action="/shoutout" method="post" class="so-form"><input type="text" name="target_name" placeholder="英語IDを入力 (例: iniwa)" required style="flex:1; padding:6px; border:1px solid #ddd; border-radius:4px;"><button class="btn btn-apply" style="padding:6px 15px; font-size:0.8em;">📣 紹介</button></form>
        </div>

        <div class="card" style="order: {{ config.layout.cards.presets.order }}; grid-column: span {{ config.layout.cards.presets.span }}; {% if config.layout.cards.presets.height > 0 %}height: {{ config.layout.cards.presets.height }}px;{% endif %}">
            <h2>
                <div>📂 プリセット</div>
                <div style="display:flex; align-items:center; gap:10px;">
                    <button class="btn btn-x" onclick="tweetStream()" title="現在設定中のタイトルでツイートを作成">
                        <span style="font-weight:bold; font-size:1.2em;">𝕏</span> ポスト
                    </button>
                    <div style="font-size:0.7em; background:#eef2f7; padding:4px 8px; border-radius:4px; font-weight:normal;">
                        現在: <b>{{ current_game if current_game else "---" }}</b>
                    </div>
                </div>
            </h2>
            
            <div class="card-scroll-area">
                <form action="/apply_preset" method="post">
                {% for preset in config.presets %}
                <div style="background:#fff8e1; border:1px solid #ffe0b2; padding:10px; border-radius:6px; margin-bottom:8px;">
                    <div style="display:flex; justify-content:space-between; align-items:flex-start;">
                        <div>
                            <div style="font-weight:bold; color:#e65100;">{{ preset.name }}</div>
                            <div style="font-size:0.85em; color:#555;">{{ preset.game }}</div>
                        </div>
                        <button type="submit" name="preset_index" value="{{ loop.index0 }}" class="btn btn-apply" onclick="return confirm('配信情報を変更しますか？')">🚀</button>
                    </div>
                    
                    {% if preset.get('tags') %}
                    <div style="margin-top:4px;">
                        {% for t in preset.tags %}
                        <span class="tag-badge">{{ t }}</span>
                        {% endfor %}
                    </div>
                    {% endif %}
                    
                    <div style="margin-top:5px; font-size:0.8em; color:#888; display:flex; align-items:center; gap: 5px;">
                        <span style="white-space:nowrap; overflow:hidden; text-overflow:ellipsis; flex: 1; min-width: 0;" title="{{ preset.title }}">{{ preset.title }}</span>
                        <span style="flex-shrink: 0; display:flex; gap: 2px;">
                            <button type="button" class="btn btn-edit" 
                                onclick="openEditPreset({{ loop.index0 }}, 
                                '{{ preset.name|replace("'", "\\'")|replace('"', '&quot;') }}', 
                                '{{ preset.game|replace("'", "\\'")|replace('"', '&quot;') }}', 
                                '{{ preset.title|replace("'", "\\'")|replace('"', '&quot;') }}',
                                '{{ (preset.tags|join(', '))|replace("'", "\\'")|replace('"', '&quot;') }}',
                                '{{ (preset.tweet_tags if preset.tweet_tags else "")|replace("'", "\\'")|replace('"', '&quot;') }}')">✏️</button>
                            <button type="submit" formaction="/delete_preset/{{ loop.index0 }}" class="btn btn-danger" style="padding:2px 6px;">🗑️</button>
                        </span>
                    </div>
                </div>
                {% endfor %}
                </form>
            </div>
            
            <form id="addPresetForm" action="/add_preset" method="post" style="border-top:1px solid #eee; padding-top:10px;">
                <div style="margin-bottom:5px; text-align:right;">
                    <button type="button" class="btn btn-secondary" style="font-size:0.8em; padding:2px 8px;" onclick="fetchCurrentSettings()">🔄 現在の設定を取得</button>
                </div>
                <div style="display:flex; gap:5px; margin-bottom:5px;">
                    <input type="text" name="name" placeholder="名称" style="flex:1;" required>
                    <input type="text" name="game" list="existing_games" placeholder="ゲーム名" style="flex:1;" required>
                </div>
                <div style="margin-bottom:5px;">
                    <input type="text" name="tags" placeholder="Twitchタグ (カンマ区切り)" style="width:100%;">
                </div>
                <div style="margin-bottom:5px;">
                    <input type="text" name="tweet_tags" placeholder="ツイート用タグ (例: #Twitch #FPS #配信)" style="width:100%;">
                </div>
                <div style="display:flex; gap:5px;">
                    <input type="text" name="title" placeholder="タイトル" style="flex:1;" required>
                    <button class="btn btn-info">＋ 追加</button>
                </div>
            </form>
        </div>

        <div class="card" style="order: {{ config.layout.cards.prediction.order }}; grid-column: span {{ config.layout.cards.prediction.span }}; {% if config.layout.cards.prediction.height > 0 %}height: {{ config.layout.cards.prediction.height }}px;{% endif %}">
            <h2>🎰 チャンネルポイント予想 (賭博)</h2>
            {% if current_prediction %}
                <div class="card-scroll-area">
                    <div style="background:#e3f2fd; padding:15px; border-radius:8px; border:1px solid #bbdefb; margin-bottom:15px;">
                        <div style="font-weight:bold; font-size:1.1em; color:#1565c0; display:flex; justify-content:space-between;">
                            <span>進行中: {{ current_prediction.title }}</span>
                            <span style="font-size:0.8em; background:white; padding:2px 8px; border-radius:10px;">
                                {% set p_start = current_prediction.created_at | to_datetime %}
                                {% if p_start %}
                                    残り: {{ (p_start + (current_prediction.prediction_window_seconds | seconds) - now) | duration_format }} 頃まで
                                {% else %}
                                    残り: 集計中...
                                {% endif %}
                            </span>
                        </div>
                        {% set ns = namespace(total_points=0, total_users=0) %}
                        {% for o in current_prediction.outcomes %}
                            {% set ns.total_points = ns.total_points + o.channel_points %}
                            {% set ns.total_users = ns.total_users + o.users %}
                        {% endfor %}
                        <div style="font-size:0.9em; color:#555; margin:10px 0;">
                            参加者: <b>{{ ns.total_users }}人</b> / 総ポイント: <b>{{ ns.total_points }} pt</b>
                        </div>
                        <div style="display:flex; height:20px; width:100%; background:#eee; border-radius:10px; overflow:hidden; margin-bottom:15px;">
                            {% for o in current_prediction.outcomes %}
                                {% if ns.total_points > 0 %}{% set pct = (o.channel_points / ns.total_points * 100) %}{% else %}{% set pct = 0 %}{% endif %}
                                <div style="width:{{ pct }}%; background:{{ 'blue' if loop.index0==0 else ('deeppink' if loop.index0==1 else '#ff9800') }};" title="{{ o.title }}: {{ o.channel_points }}pt"></div>
                            {% endfor %}
                        </div>
                        <form action="/resolve_prediction" method="post" style="display:flex; flex-direction:column; gap:5px;">
                            <input type="hidden" name="prediction_id" value="{{ current_prediction.id }}">
                            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap:10px;">
                                {% for outcome in current_prediction.outcomes %}
                                    <button name="winning_outcome_id" value="{{ outcome.id }}" class="btn" onclick="return confirm('「{{ outcome.title }}」を勝者として確定しますか？')" 
                                    style="background:{{ 'blue' if loop.index0 == 0 else ('deeppink' if loop.index0 == 1 else '#fff') }}; 
                                        color:{{ '#fff' if loop.index0 < 2 else '#333' }}; border:1px solid #ccc; opacity:0.9; text-align:left; padding:10px;">
                                        <div style="font-weight:bold;">{{ outcome.title }}</div>
                                        <div style="font-size:0.8em;">{{ outcome.users }}人 / {{ outcome.channel_points }}pt</div>
                                    </button>
                                {% endfor %}
                            </div>
                            <button name="cancel" value="1" class="btn btn-secondary" onclick="return confirm('予想をキャンセルしてポイントを返却しますか？')" style="margin-top:10px; align-self:flex-start;">🚫 キャンセル (返金)</button>
                        </form>
                    </div>
                </div>
            {% else %}
                <div class="card-scroll-area">
                    <form action="/start_prediction" method="post">
                        {% for preset in config.get('prediction_presets', []) %}
                            <div style="background:#f3e5f5; border:1px solid #e1bee7; padding:8px; border-radius:6px; margin-bottom:5px; display:flex; justify-content:space-between; align-items:center;">
                                <div style="flex:1;">
                                    <div style="font-weight:bold; color:#7b1fa2;">{{ preset.title }}</div>
                                    <div style="font-size:0.8em; color:#666;">
                                        選択肢: {{ preset.outcomes | join(' / ') }} ({{ preset.duration }}秒)
                                    </div>
                                </div>
                                <div style="display:flex; gap:5px;">
                                    <button type="submit" name="preset_index" value="{{ loop.index0 }}" class="btn btn-so" onclick="return confirm('この予想を開始しますか？')">開始</button>
                                    <button type="button" class="btn btn-edit" 
                                        data-title="{{ preset.title|forceescape }}" 
                                        data-outcomes="{{ preset.outcomes|tojson|forceescape }}"
                                        onclick="openEditPredictionModal({{ loop.index0 }}, this.getAttribute('data-title'), {{ preset.duration }}, this.getAttribute('data-outcomes'))">✏️</button>
                                    <button type="submit" formaction="/delete_prediction_preset/{{ loop.index0 }}" class="btn btn-danger" style="padding:2px 6px;">🗑️</button>
                                </div>
                            </div>
                        {% endfor %}
                    </form>
                </div>
                <form action="/add_prediction_preset" method="post" style="border-top:1px solid #eee; padding-top:10px;">
                    <div style="font-weight:bold; color:#555; margin-bottom:5px;">新規作成</div>
                    <div class="form-group"><input type="text" name="title" placeholder="予想タイトル" required></div>
                    <div id="new_pred_outcomes_container">
                        <div style="display:flex; gap:5px; margin-bottom:5px;"><input type="text" name="outcomes" placeholder="選択肢 1" style="flex:1;" required></div>
                        <div style="display:flex; gap:5px; margin-bottom:5px;"><input type="text" name="outcomes" placeholder="選択肢 2" style="flex:1;" required></div>
                    </div>
                    <button type="button" class="btn btn-secondary" style="font-size:0.8em; margin-bottom:10px;" onclick="addOutcomeInput('new_pred_outcomes_container')">＋ 選択肢を増やす</button>
                    <div style="display:flex; gap:5px;">
                        <input type="number" name="duration" placeholder="秒数" value="120" style="width:80px;" required>
                        <button class="btn btn-info" style="flex:1;">保存</button>
                    </div>
                </form>
            {% endif %}
        </div>

        <div class="card" style="order: {{ config.layout.cards.rules.order }}; grid-column: span {{ config.layout.cards.rules.span }}; {% if config.layout.cards.rules.height > 0 %}height: {{ config.layout.cards.rules.height }}px;{% endif %}">
            <h2>🎮 自動チャットルール</h2><div class="filter-bar"><button class="filter-btn active" onclick="filterRules('__ALL__', this)">全て表示</button>{% for g in unique_games %}<button class="filter-btn" onclick="filterRules('{{ g }}', this)">{{ g }}</button>{% endfor %}</div>
            <div class="card-scroll-area">
                <form id="saveRulesForm" action="/save_rules" method="post">{% for rule in config.rules %}<div class="rule-card" data-game="{{ rule.game }}"><div class="rule-header"><div style="display:flex; gap:5px; align-items:center; flex:1;"><span style="font-weight:bold; color:#888; font-size:0.8em;">#{{ loop.index }}</span><input type="text" name="name" value="{{ rule.name }}" placeholder="ルール名" style="font-weight:bold; width:120px; border:none; background:transparent; border-bottom:1px solid #ccc;"><input type="text" name="game" list="existing_games" value="{{ rule.game }}" placeholder="Game" style="width:150px; color:#555;"></div><div style="text-align:right;">{% if rule.is_active %}<span class="status-active">✅ 稼働中</span><span class="timer-info">{{ rule.next_run }}</span>{% else %}<span class="status-sleep">💤 待機中 ({{ rule.reason }})</span>{% endif %}</div></div><div class="rule-body"><input type="text" name="message" value="{{ rule.message }}" placeholder="送信メッセージ" style="width:100%; padding:8px; border:1px solid #ddd; border-radius:4px;"></div><div class="rule-footer"><div style="font-size:0.85em; display:flex; gap:10px; align-items:center; flex-wrap: wrap;"><span>間隔(分): <input type="number" name="interval" value="{{ rule.interval }}" class="input-mini"></span><span>必要数: <input type="number" name="min_comments" value="{{ rule.min_comments }}" class="input-mini">{% if rule.is_active and rule.remaining_comments > 0 %}<span class="remaining-info">あと {{ rule.remaining_comments }}</span>{% endif %}</span></div><div style="display:flex; gap:2px;">{% if not loop.first %}<button type="submit" formaction="/move_rule/{{ loop.index0 }}/up" class="btn btn-move">▲</button>{% endif %}{% if not loop.last %}<button type="submit" formaction="/move_rule/{{ loop.index0 }}/down" class="btn btn-move">▼</button>{% endif %}<button type="submit" formaction="/delete_rule/{{ loop.index0 }}" class="btn btn-danger" style="margin-left:5px;">削除</button></div></div></div>{% endfor %}</form>
            </div>
            <div style="display:flex; justify-content:space-between; margin-top:5px; flex-shrink:0;">
                <button type="button" onclick="toggleModal('addRuleModal')" class="btn btn-info">＋ ルール追加</button>
                <button type="submit" form="saveRulesForm" class="btn btn-primary" style="padding:8px 30px;">全ルールを保存</button>
            </div>
            </div>

        <div class="card" style="order: {{ config.layout.cards.logs.order }}; grid-column: span {{ config.layout.cards.logs.span }}; {% if config.layout.cards.logs.height > 0 %}height: {{ config.layout.cards.logs.height }}px;{% endif %}">
            <h2><div style="display:flex; align-items:center; gap:10px;">📜 ログ <span class="status-badge {{ 'running' if config.is_running else 'stopped' }}">{{ "🟢 稼働中" if config.is_running else "🔴 停止中" }}</span></div><div class="control-group"><form action="/toggle" method="post" style="display:inline;"><button class="btn {{ 'btn-stop' if config.is_running else 'btn-success' }}" style="padding:4px 10px; font-size:0.85em;">{{ '停止' if config.is_running else '起動' }}</button></form><form action="/test_message" method="post" style="display:inline;"><button class="btn btn-apply" style="padding:4px 10px; font-size:0.85em;">🔔 テスト</button></form><button class="btn btn-secondary" onclick="toggleModal('settingsModal')" style="padding:4px 10px; font-size:0.85em;">⚙️ 設定</button></div></h2>
            <div class="logs card-scroll-area" id="log-container">{% for log in logs %}<div>{{ log }}</div>{% endfor %}</div>
        </div>
    </div>
    
<div id="addRuleModal" class="modal">
    <div class="modal-content">
        <span class="close" onclick="toggleModal('addRuleModal')">&times;</span>
        <h2 style="border:none;">＋ 新しいルールを追加</h2>
        <form action="/add_rule" method="post">
            <div class="form-group">
                <label>ルール名</label>
                <input type="text" name="name" value="ルール #{{ config.rules|length + 1 }}" required>
            </div>
            <div class="form-group">
                <label>ゲーム</label>
                <input type="text" name="game" list="existing_games" value="All" required>
            </div>
            <div class="form-group">
                <label>メッセージ</label>
                <textarea name="message" rows="3" required></textarea>
            </div>
            <div style="display:flex; gap:15px;">
                <div class="form-group" style="flex:1;">
                    <label>間隔 (分)</label>
                    <input type="number" name="interval" value="15" required>
                </div>
                <div class="form-group" style="flex:1;">
                    <label>必要コメント数</label>
                    <input type="number" name="min_comments" value="2" required>
                </div>
            </div>
            <div style="margin-top:20px; text-align:right;">
                <button class="btn btn-primary">追加する</button>
            </div>
        </form>
    </div>
</div>
    
<div id="settingsModal" class="modal">
    <div class="modal-content">
        <span class="close" onclick="toggleModal('settingsModal')">&times;</span>
        <h2 style="border:none;">⚙️ Bot基本設定</h2>
        <form action="/save_config" method="post">
            <div class="form-group"><label>Client ID</label><input type="text" name="client_id" value="{{ config.client_id }}"></div>
            <div class="form-group"><label>Bot Access Token</label><input type="password" name="access_token" value="{{ config.access_token }}"></div>
            <div class="form-group"><label>Broadcaster Token</label><input type="password" name="broadcaster_token" value="{{ config.get('broadcaster_token', '') }}"></div>
            <div class="form-group"><label>Broadcaster ID</label><input type="text" name="broadcaster_id" value="{{ config.broadcaster_id }}"></div>
            <div class="form-group"><label>Bot User ID</label><input type="text" name="bot_user_id" value="{{ config.bot_user_id }}"></div>
            <div class="form-group"><label>Channel Name</label><input type="text" name="channel_name" value="{{ config.channel_name }}"></div>
            <div style="margin-top:20px; padding:10px; background:#f9f9f9; border-radius:5px;">
                <label style="display:inline-block; margin-right:15px; margin-bottom:5px;"><input type="checkbox" name="debug_mode" {% if config.debug_mode %}checked{% endif %}> デバッグモード</label><br>
                <label style="display:inline-block; margin-right:15px; margin-bottom:5px;"><input type="checkbox" name="ignore_stream_status" {% if config.ignore_stream_status %}checked{% endif %}> 配信外でもBotを稼働する</label><br>
                <label style="display:inline-block; margin-top:5px; font-weight:bold; color:#e91e63;"><input type="checkbox" name="enable_welcome" {% if config.enable_welcome %}checked{% endif %}> ✨ 初コメント時に「ようこそ！」と挨拶する</label><br>
                <label style="display:inline-block; margin-top:5px; font-weight:bold; color:#6441a5;"><input type="checkbox" name="hide_self_bot" {% if config.get('hide_self_bot') %}checked{% endif %}> 👻 配信者本人とBotをリストから隠す</label>
                <div style="margin-top:10px; padding:10px; background:#e8f5e9; border:1px solid #c8e6c9; border-radius:4px;">
                    <label style="display:inline-block; font-weight:bold; color:#2e7d32;"><input type="checkbox" name="enable_vod_download" {% if config.get('enable_vod_download') %}checked{% endif %}> 🎥 配信終了後にアーカイブを自動ダウンロード</label>
                    <div style="margin-top:5px; font-size:0.85em; color:#555;">保存先: <span style="font-family:monospace;">/app/downloads</span> (固定)</div>
                </div>
                <div style="margin-top:10px;">
                    <label style="color:#555;">🚫 非表示ユーザーリスト</label>
                    <textarea name="ignored_users_list" rows="3">{{ config.get('ignored_users', []) | join('\n') }}</textarea>
                </div>
            </div>
            <div style="margin-top:15px; padding-top:15px; border-top:1px dashed #ddd;">
                <label style="color:#333; font-weight:bold;">🎨 レイアウト設定</label>
                <div style="display:flex; gap:10px; margin-bottom:10px; margin-top:5px;">
                    <div style="flex:1;">全体マス数: <input type="number" name="layout_columns" value="{{ config.layout.columns }}" min="1" max="6" style="width:60px; padding:4px;"></div>
                    <div style="flex:1;">全体最大幅(px): <input type="number" name="layout_max_width" value="{{ config.layout.max_width }}" style="width:80px; padding:4px;"></div>
                </div>
                <table class="layout-table">
                    <thead><tr><th>パネル名</th><th>順番</th><th>横幅(マス)</th><th>縦幅(px)<br><span style="font-size:0.8em; font-weight:normal;">(0=自動)</span></th></tr></thead>
                    <tbody>
                        <tr><td>視聴者リスト</td>
                            <td><input type="number" name="layout_viewers_order" value="{{ config.layout.cards.viewers.order }}"></td>
                            <td><input type="number" name="layout_viewers_span" value="{{ config.layout.cards.viewers.span }}" min="1"></td>
                            <td><input type="number" name="layout_viewers_height" value="{{ config.layout.cards.viewers.height }}" min="0"></td>
                        </tr>
                        <tr><td>プリセット</td>
                            <td><input type="number" name="layout_presets_order" value="{{ config.layout.cards.presets.order }}"></td>
                            <td><input type="number" name="layout_presets_span" value="{{ config.layout.cards.presets.span }}" min="1"></td>
                            <td><input type="number" name="layout_presets_height" value="{{ config.layout.cards.presets.height }}" min="0"></td>
                        </tr>
                        <tr><td>予想 (賭博)</td>
                            <td><input type="number" name="layout_prediction_order" value="{{ config.layout.cards.prediction.order }}"></td>
                            <td><input type="number" name="layout_prediction_span" value="{{ config.layout.cards.prediction.span }}" min="1"></td>
                            <td><input type="number" name="layout_prediction_height" value="{{ config.layout.cards.prediction.height }}" min="0"></td>
                        </tr>
                        <tr><td>ルール</td>
                            <td><input type="number" name="layout_rules_order" value="{{ config.layout.cards.rules.order }}"></td>
                            <td><input type="number" name="layout_rules_span" value="{{ config.layout.cards.rules.span }}" min="1"></td>
                            <td><input type="number" name="layout_rules_height" value="{{ config.layout.cards.rules.height }}" min="0"></td>
                        </tr>
                        <tr><td>ログ</td>
                            <td><input type="number" name="layout_logs_order" value="{{ config.layout.cards.logs.order }}"></td>
                            <td><input type="number" name="layout_logs_span" value="{{ config.layout.cards.logs.span }}" min="1"></td>
                            <td><input type="number" name="layout_logs_height" value="{{ config.layout.cards.logs.height }}" min="0"></td>
                        </tr>
                    </tbody>
                </table>
            </div>
            <div style="margin-top:20px; text-align:right;"><button class="btn btn-primary">保存して閉じる</button></div>
        </form>
        <div style="margin-top:15px; padding-top:15px; border-top:1px dashed #ddd;">
            <label style="color:#555; font-weight:bold;">🛠️ デバッグアクション</label>
            <div style="margin-top:5px; display:flex; gap:5px; flex-wrap:wrap;">
                <form action="/debug_update_followers" method="post" onsubmit="return confirm('API実行しますか？');" style="flex:1;">
                    <button class="btn btn-info" style="width:100%;">🔄 フォロワー更新</button>
                </form>
                <a href="/debug_check" target="_blank" class="btn btn-secondary" style="flex:1; text-align:center; padding-top:8px;">🏥 データ取得診断</a>
                <form action="/delete_debug_history" method="post" onsubmit="return confirm('デバッグ履歴（ログ・統計・録画）を完全に削除しますか？\\nこの操作は取り消せません。');" style="flex:1;">
                    <button class="btn btn-danger" style="width:100%;">🗑️ デバッグ履歴削除</button>
                </form>
            </div>
        </div>
    </div>
</div>
    
    <div id="editPresetModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="toggleModal('editPresetModal')">&times;</span>
            <h2 style="border:none;">📝 プリセット編集</h2>
            <form action="/update_preset" method="post">
                <input type="hidden" id="edit_preset_index" name="preset_index">
                <div class="form-group"><label>プリセット名</label><input type="text" id="edit_preset_name" name="name" required></div>
                <div class="form-group"><label>ゲームカテゴリー</label><input type="text" id="edit_preset_game" name="game" list="existing_games" required></div>
                
                <div class="form-group"><label>タグ (カンマ区切り)</label><input type="text" id="edit_preset_tags" name="tags" placeholder="FPS, 参加型, 初心者歓迎"></div>
                
                <div class="form-group"><label>ツイート用タグ</label><input type="text" id="edit_preset_tweet_tags" name="tweet_tags" placeholder="#Twitch #配信 #FPS"></div>
                
                <div class="form-group"><label>配信タイトル</label><input type="text" id="edit_preset_title" name="title" required></div>
                <div style="margin-top:20px; text-align:right;"><button class="btn btn-primary">変更を保存</button></div>
            </form>
        </div>
    </div>
    
    <div id="historyModal" class="modal">
        <div class="modal-content" style="max-width:1000px; height:90vh; display:flex; flex-direction:column;">
            <div style="flex-shrink:0;">
                <span class="close" onclick="toggleModal('historyModal')">&times;</span>
                <h2 style="border:none; margin:0; padding-bottom:10px;">📜 視聴者履歴 (全期間)</h2>
            </div>
            <div style="flex:1; overflow-y:auto; min-height:0;">
                <table class="history-table" id="historyTable">
                    <thead><tr><th onclick="sortTable('historyTable', 0)">名前 (ID)</th><th onclick="sortTable('historyTable', 1)">フォロー</th><th onclick="sortTable('historyTable', 2)">来場回数</th><th onclick="sortTable('historyTable', 3)">累計滞在</th><th onclick="sortTable('historyTable', 4)">コメント</th><th onclick="sortTable('historyTable', 5)">ビッツ</th><th onclick="sortTable('historyTable', 6)">サブスク</th><th onclick="sortTable('historyTable', 7)">連続</th><th onclick="sortTable('historyTable', 8)">メモ</th><th onclick="sortTable('historyTable', 9)">最終退出</th></tr></thead>
                    <tbody id="history-tbody">
                        {% for uid, hist in history.items() %}
                        <tr>
                            <td>{{ hist.name }} <br><span style="font-size:0.8em; color:#888;">({{ hist.login if hist.login else "-" }})</span></td>
                            <td data-sort="{{ hist.followed_at|replace('-','') if hist.is_follower and hist.followed_at else 0 }}">
                                {% if hist.is_follower %}<div class="follow-status">✅</div><div style="font-size:0.75em; color:#888;">{{ hist.followed_at }}~</div>{% else %}-{% endif %}
                            </td>
                            <td data-sort="{{ hist.total_visits }}">{{ hist.total_visits }}回</td>
                            <td data-sort="{{ hist.total_duration if hist.total_duration else 0 }}">{{ hist.total_duration | duration_format }}</td>
                            <td data-sort="{{ hist.total_comments if hist.total_comments else 0 }}">{{ hist.total_comments if hist.total_comments else 0 }}回</td>
                            <td data-sort="{{ hist.total_bits if hist.total_bits else 0 }}" class="{{ 'bits-count' if hist.total_bits else '' }}">{{ hist.total_bits if hist.total_bits else 0 }}</td>
                            <td data-sort="{{ 1 if hist.is_sub else 0 }}" class="{{ 'sub-active' if hist.is_sub else '' }}">{{ "⭐" if hist.is_sub else "-" }}</td>
                            <td data-sort="{{ hist.get('streak', 0) }}">
                                {% if hist.get('streak', 0) > 1 %}🔥{{ hist.get('streak', 0) }}{% else %}-{% endif %}
                            </td>
                            <td>
                                <div class="memo-text">{{ hist.memo if hist.memo else "" }}</div>
                                <button class="btn-memo" onclick="openMemoModal('{{ uid }}', '{{ hist.name|replace("'", "\\'")|replace('"', '&quot;') }}', '{{ (hist.memo if hist.memo else "")|replace("'", "\\'")|replace('"', '&quot;') }}')">📝</button>
                            </td>
                            <td data-sort="{{ 9999999999 if uid in active_uids else (hist.last_seen_ts if hist.last_seen_ts else 0) }}">
                                {% if uid in active_uids %}<span class="watching-status">視聴中</span>{% else %}{{ hist.last_seen_ts | timestamp_to_date }}{% endif %}
                            </td>
                        </tr>
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <div id="followerModal" class="modal">
        <div class="modal-content" style="max-width:800px; height:90vh; display:flex; flex-direction:column;">
            <div style="flex-shrink:0;">
                <span class="close" onclick="toggleModal('followerModal')">&times;</span>
                <h2 style="border:none; margin:0; padding-bottom:10px;">👥 フォロワーリスト</h2>
            </div>
            <div style="flex:1; overflow-y:auto; min-height:0;">
                <table class="history-table" id="followerTable">
                    <thead><tr><th onclick="sortTable('followerTable', 0)">名前 (ID)</th><th onclick="sortTable('followerTable', 1)">状態</th><th onclick="sortTable('followerTable', 2)">フォロー開始</th><th onclick="sortTable('followerTable', 3)">フォロー解除</th></tr></thead>
                    <tbody>
                        {% for uid, follower in history.items() %}
                        {% if follower.is_follower or follower.unfollowed_at %}
                        <tr>
                            <td><b>{{ follower.name }}</b><br><span style="font-size:0.8em; color:#888;">({{ follower.login }})</span></td>
                            <td data-sort="{{ 1 if follower.is_follower else 0 }}">
                                {% if follower.is_follower %}<span class="follow-status">✅ フォロー中</span>{% else %}<span class="unfollow-status">💔 解除済み</span>{% endif %}
                            </td>
                            <td data-sort="{{ follower.followed_at|replace('-','') if follower.followed_at else 0 }}">{{ follower.followed_at }}</td>
                            <td data-sort="{{ follower.unfollowed_at|replace('-','') if follower.unfollowed_at else 0 }}">{{ follower.unfollowed_at }}</td>
                        </tr>
                        {% endif %}
                        {% endfor %}
                    </tbody>
                </table>
            </div>
        </div>
    </div>
    <div id="editPredictionModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="toggleModal('editPredictionModal')">&times;</span>
            <h2 style="border:none;">🎰 予想プリセットの編集</h2>
            <form action="/update_prediction_preset" method="post">
                <input type="hidden" id="edit_pred_index" name="preset_index">
                
                <div class="form-group">
                    <label>タイトル</label>
                    <input type="text" id="edit_pred_title" name="title" required>
                </div>
                
                <div class="form-group">
                    <label>選択肢 (最大10個)</label>
                    <div id="edit_pred_outcomes_container"></div>
                    <button type="button" class="btn btn-secondary" style="font-size:0.8em; margin-top:5px;" onclick="addOutcomeInput('edit_pred_outcomes_container')">＋ 選択肢を追加</button>
                </div>
                
                <div class="form-group">
                    <label>受付時間 (秒)</label>
                    <input type="number" id="edit_pred_duration" name="duration" required>
                </div>
                
                <div style="margin-top:20px; text-align:right;">
                    <button class="btn btn-primary">変更を保存</button>
                </div>
            </form>
        </div>
    </div>
</body>
</html>
"""

# ... (ANALYTICS_TEMPLATE は変更がないため省略します) ...

# --- ★強化版アナリティクス画面 ---
ANALYTICS_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Analytics - Twitch Bot</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
    <style>
        body { font-family: "Helvetica Neue", Arial, sans-serif; margin: 0; padding: 20px; background: #f4f6f9; color: #333; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        .card { background: white; padding: 20px; border-radius: 12px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); margin-bottom: 20px; }
        h2 { margin-top: 0; color: #2c3e50; border-bottom: 2px solid #f0f0f0; padding-bottom: 10px; display:flex; justify-content:space-between; align-items:center; }
        .btn { padding: 8px 16px; border: none; border-radius: 6px; cursor: pointer; color: white; text-decoration: none; display: inline-block; font-size: 0.9em; }
        .btn-back { background: #6c757d; }
        .btn-dl { background: #009688; padding:4px 8px; font-size:0.8em; }
        .btn-stop { background: #e74c3c; padding:4px 8px; font-size:0.8em; }
        .btn-del { background: #d73a49; padding:4px 8px; font-size:0.8em; }
        .btn-bulk { background: #e91e63; }
        .btn-edit { background: #007bff; padding:4px 8px; font-size:0.8em; margin-right: 5px; }
        .btn-secondary { background: #6c757d; color: white; } /* 色を調整 */
        .btn-secondary:hover { background: #5a6268; }
        
        /* タブ切り替え */
        .tab-menu { display: flex; gap: 10px; margin-bottom: 15px; border-bottom: 1px solid #ddd; padding-bottom: 10px; }
        .tab-btn { padding: 10px 20px; border: none; background: #eee; cursor: pointer; border-radius: 5px; font-weight: bold; color: #555; }
        .tab-btn.active { background: #6441a5; color: white; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }

        /* グラフコントロール */
        .chart-controls { display: flex; gap: 10px; margin-bottom: 15px; align-items: center; flex-wrap: wrap; background: #f8f9fa; padding: 10px; border-radius: 8px; }
        .range-btn { padding: 5px 12px; border: 1px solid #ccc; background: white; cursor: pointer; border-radius: 4px; font-size: 0.9em; }
        .range-btn.active { background: #6441a5; color: white; border-color: #6441a5; }
        .date-input { padding: 5px; border: 1px solid #ddd; border-radius: 4px; }

        /* テーブル */
        table { width: 100%; border-collapse: collapse; table-layout: fixed; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #eee; word-wrap: break-word; }
        th { background-color: #f8f9fa; font-size: 0.9em; color: #555; cursor: pointer; user-select: none; }
        th:hover { background-color: #e9ecef; }
        th::after { content: ' ↕'; font-size: 0.7em; color: #ccc; }
        tr:hover { background-color: #f1f1f1; }
        
        /* 検索ボックス */
        .search-box { width: 100%; padding: 10px; margin-bottom: 15px; border: 1px solid #ddd; border-radius: 5px; font-size: 1em; box-sizing: border-box; }

        /* カレンダー */
        .calendar-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .calendar-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 5px; min-width: 800px; overflow-x: auto; } /* レイアウト修正: min-width追加 */
        .cal-day-header { text-align: center; font-weight: bold; color: #666; padding: 5px; }
        .cal-day { background: #fff; border: 1px solid #eee; min-height: 80px; padding: 5px; position: relative; border-radius: 4px; }
        .cal-day:hover { background: #fafafa; }
        .cal-date-num { font-size: 0.9em; font-weight: bold; color: #333; margin-bottom: 5px; }
        .cal-event { background: #e3f2fd; color: #1565c0; font-size: 0.75em; padding: 2px 4px; border-radius: 3px; margin-bottom: 2px; cursor: pointer; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
        .cal-event:hover { background: #bbdefb; }
        .other-month { background: #f9f9f9; color: #ccc; }

        /* カラム幅 */
        .col-date { width: 110px; }
        .col-title { width: auto; }
        .col-game { width: 120px; }
        .col-duration { width: 80px; }
        .col-source { width: 80px; }
        .col-viewers { width: 90px; }
        .col-archive { width: 120px; }
        .col-action { width: 100px; }

        /* その他スタイル */
        .stat-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .stat-box { background: #fff; padding: 15px; border-radius: 8px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.05); border:1px solid #eee; }
        .stat-val { font-size: 1.5em; font-weight: bold; color: #6441a5; }
        .stat-label { font-size: 0.85em; color: #666; margin-top:5px; }
        .chart-container { position: relative; height: 350px; width: 100%; }
        .chat-log { height: 350px; overflow-y: scroll; background: #fafafa; border: 1px solid #eee; padding: 10px; font-family: monospace; font-size: 0.9em; }
        .chat-line { margin-bottom: 4px; border-bottom: 1px dashed #eee; padding-bottom: 2px; }
        .ts { color: #888; font-size: 0.8em; margin-right: 5px; }
        .usr { font-weight: bold; color: #007bff; } .sub { color: #e91e63; font-weight: bold; }
        .pt { color: #009688; font-weight:bold; }
        .status-dl { color:green; font-weight:bold; } .status-wait { color:#f57c00; font-weight:bold; } .status-fail { color:red; }
        .source-bot { background:#e8f5e9; color:#2e7d32; padding:2px 4px; border-radius:4px; font-size:0.75em; border:1px solid #c8e6c9; display:inline-block; text-align:center; min-width:50px;}
        .source-api { background:#e3f2fd; color:#1565c0; padding:2px 4px; border-radius:4px; font-size:0.75em; border:1px solid #bbdefb; display:inline-block; text-align:center; min-width:50px;}
        .nowrap { white-space: nowrap; }
        .date-main { font-weight:bold; font-size: 0.9em; }
        .date-sub { font-size: 0.8em; color: #888; }
        
        /* ページネーション */
        .pagination { display: flex; justify-content: center; gap: 10px; margin-top: 15px; align-items: center; }
        .page-info { font-size: 0.9em; color: #666; }
        
        /* モーダル */
        .modal { display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.5); }
        .modal-content { background-color: white; margin: 10vh auto; padding: 25px; border-radius: 10px; width: 90%; max-width: 500px; position: relative; box-shadow: 0 4px 15px rgba(0,0,0,0.2); }
        .close { float: right; font-size: 24px; font-weight: bold; cursor: pointer; color: #aaa; }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; margin-bottom: 5px; font-weight: bold; font-size: 0.9em; color: #555; }
        .form-group input { width: 100%; padding: 8px; box-sizing: border-box; border: 1px solid #ddd; border-radius: 4px; }
    </style>
    <script>
        // --- データ受け渡し ---
        const allStreams = {{ all_streams_json | safe }};
        const followerHistory = {{ follower_history_json | safe }};
        let trendChart = null;
        let timelineChart = null; // 週間チャート用
        
        // ページネーション用変数
        let currentPage = 1;
        const itemsPerPage = 50;
        let currentFilteredData = [];
        
        // タイムライン用変数
        let timelineEndDate = new Date();

        // --- タブ切り替え ---
        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            document.getElementById('btn-' + tabId).classList.add('active');
            if(tabId === 'calendar') {
                renderCalendar(currentYear, currentMonth);
                renderWeeklyTimeline(); // 追加: 週間チャート描画
            }
            if(tabId === 'trends') initTrendPage();
        }
        
        function initTrendPage() {
            if(!trendChart) setRange('1M');
        }

        function setRange(rangeType) {
            document.querySelectorAll('.range-btn').forEach(b => b.classList.remove('active'));
            const btn = document.getElementById('btn-range-' + rangeType);
            if(btn) btn.classList.add('active');

            const now = new Date();
            let startDate = new Date();
            let endDate = new Date();

            if (rangeType === '1W') { startDate.setDate(now.getDate() - 7); }
            else if (rangeType === '1M') { startDate.setMonth(now.getMonth() - 1); }
            else if (rangeType === '1Y') { startDate.setFullYear(now.getFullYear() - 1); }
            else if (rangeType === 'ALL') { startDate = new Date(2020, 0, 1); }
            else if (rangeType === 'CUSTOM') {
                const s = document.getElementById('start-date').value;
                const e = document.getElementById('end-date').value;
                if(s) startDate = new Date(s);
                if(e) endDate = new Date(e);
                endDate.setHours(23, 59, 59);
            }
            updateTrendChart(startDate, endDate);
        }

        function updateTrendChart(startDate, endDate) {
            const filtered = allStreams.filter(s => {
                const d = new Date(s.start_time);
                return d >= startDate && d <= endDate;
            }).sort((a,b) => new Date(a.start_time) - new Date(b.start_time));

            // チャート描画
            renderChart(filtered, startDate, endDate);
            
            // 統計計算と表示
            calculateAndShowStats(filtered);

            // リスト描画（ページネーション初期化）
            currentFilteredData = [...filtered].reverse(); // 新しい順
            currentPage = 1;
            renderFilteredList();
        }
        
        // ★追加: 期間内の統計とゲーム別集計
        function calculateAndShowStats(data) {
            let totalAvgViewers = 0;
            let avgCount = 0;
            let totalDurationSec = 0;
            let gameStats = {};

            data.forEach(s => {
                // 平均同接の計算 (API取得分は除外)
                if (s.source !== 'api') {
                    totalAvgViewers += (s.avg_viewers || 0);
                    avgCount++;
                }
                
                // 時間計算
                let dur = 0;
                if (s.duration) {
                    const parts = s.duration.match(/(\d+)h\s*(\d+)m/) || s.duration.match(/(\d+)m/);
                    if (parts) {
                        if (parts.length === 3) dur = parseInt(parts[1])*3600 + parseInt(parts[2])*60;
                        else dur = parseInt(parts[1])*60;
                    }
                }
                totalDurationSec += dur;

                // ゲーム別集計
                const gName = s.game_name || "Unknown";
                if (!gameStats[gName]) {
                    gameStats[gName] = { count: 0, totalAvg: 0, avgCount: 0, totalDur: 0 };
                }
                gameStats[gName].count++;
                gameStats[gName].totalDur += dur;
                if (s.source !== 'api') {
                    gameStats[gName].totalAvg += (s.avg_viewers || 0);
                    gameStats[gName].avgCount++;
                }
            });

            // 全体統計表示
            const overallAvg = avgCount > 0 ? (totalAvgViewers / avgCount).toFixed(1) : "-";
            const h = Math.floor(totalDurationSec / 3600);
            const m = Math.floor((totalDurationSec % 3600) / 60);
            
            document.getElementById('stat-avg-viewers').innerText = overallAvg;
            document.getElementById('stat-total-time').innerText = `${h}h ${m}m`;
            document.getElementById('stat-stream-count').innerText = data.length + "回";

            // ゲーム別テーブル生成
            const tbody = document.getElementById('gamestats-tbody');
            tbody.innerHTML = '';
            
            // 平均同接でソート
            const sortedGames = Object.keys(gameStats).sort((a, b) => {
                const avgA = gameStats[a].avgCount > 0 ? gameStats[a].totalAvg / gameStats[a].avgCount : 0;
                const avgB = gameStats[b].avgCount > 0 ? gameStats[b].totalAvg / gameStats[b].avgCount : 0;
                return avgB - avgA;
            });

            sortedGames.forEach(g => {
                const st = gameStats[g];
                const avg = st.avgCount > 0 ? (st.totalAvg / st.avgCount).toFixed(1) : "-";
                const gh = Math.floor(st.totalDur / 3600);
                const gm = Math.floor((st.totalDur % 3600) / 60);
                tbody.innerHTML += `<tr><td>${g}</td><td>${avg}</td><td>${gh}h ${gm}m</td><td>${st.count}</td></tr>`;
            });
        }

        // --- 検索機能 (一覧) ---
        function filterTable() {
            const input = document.getElementById("searchInput");
            const filter = input.value.toLowerCase();
            const table = document.getElementById("streamTable");
            const tr = table.getElementsByTagName("tr");

            for (let i = 1; i < tr.length; i++) { // ヘッダー除外
                const tds = tr[i].getElementsByTagName("td");
                let txtValue = "";
                for(let j=0; j<tds.length; j++) { txtValue += tds[j].textContent || tds[j].innerText; }
                if (txtValue.toLowerCase().indexOf(filter) > -1) { tr[i].style.display = ""; } 
                else { tr[i].style.display = "none"; }
            }
        }

        // --- ソート機能 (一覧) ---
        function sortTable(n) {
            const table = document.getElementById("streamTable");
            let rows, switching, i, x, y, shouldSwitch, dir, switchcount = 0;
            switching = true; dir = "asc";
            
            while (switching) {
                switching = false; rows = table.rows;
                for (i = 1; i < (rows.length - 1); i++) {
                    shouldSwitch = false;
                    x = rows[i].getElementsByTagName("TD")[n];
                    y = rows[i + 1].getElementsByTagName("TD")[n];
                    let xVal = x.textContent.toLowerCase(); let yVal = y.textContent.toLowerCase();
                    if (dir == "asc") { if (xVal > yVal) { shouldSwitch = true; break; } } 
                    else if (dir == "desc") { if (xVal < yVal) { shouldSwitch = true; break; } }
                }
                if (shouldSwitch) {
                    rows[i].parentNode.insertBefore(rows[i + 1], rows[i]);
                    switching = true; switchcount ++;
                } else {
                    if (switchcount == 0 && dir == "asc") { dir = "desc"; switching = true; }
                }
            }
        }

        // --- 共通: リスト描画関数 (新規追加) ---
        function renderSimpleList(data, elementId) {
            const tbody = document.getElementById(elementId);
            if(!tbody) return;
            
            const sorted = [...data].sort((a,b) => new Date(b.start_time) - new Date(a.start_time));
            
            if (sorted.length === 0) {
                tbody.innerHTML = '<tr><td colspan="4" style="text-align:center; color:#999; padding:10px;">データなし</td></tr>';
                return;
            }

            tbody.innerHTML = sorted.map(s => `
                <tr>
                    <td style="width:120px;">${s.start_time.substring(5, 16).replace('T', ' ')}</td>
                    <td><a href="/analytics/stream/${s.sid}" target="_blank" style="font-weight:bold; color:#6441a5; text-decoration:none;">${s.title}</a></td>
                    <td>${s.game_name}</td>
                    <td style="width:80px;">${s.duration_short || '-'}</td>
                </tr>
            `).join('');
        }

        // --- カレンダー機能 ---
        let currentYear = new Date().getFullYear();
        let currentMonth = new Date().getMonth(); // 0-11
        const monthNames = ["1月", "2月", "3月", "4月", "5月", "6月", "7月", "8月", "9月", "10月", "11月", "12月"];

        function changeMonth(d) {
            currentMonth += d;
            if(currentMonth < 0) { currentMonth = 11; currentYear--; }
            else if(currentMonth > 11) { currentMonth = 0; currentYear++; }
            renderCalendar(currentYear, currentMonth);
        }

        function renderCalendar(year, month) {
            const grid = document.getElementById('calendarGrid');
            const title = document.getElementById('calendarTitle');
            grid.innerHTML = '<div class="cal-day-header">日</div><div class="cal-day-header">月</div><div class="cal-day-header">火</div><div class="cal-day-header">水</div><div class="cal-day-header">木</div><div class="cal-day-header">金</div><div class="cal-day-header">土</div>';
            title.innerText = `${year}年 ${monthNames[month]}`;

            const firstDay = new Date(year, month, 1).getDay();
            const daysInMonth = new Date(year, month + 1, 0).getDate();

            // 月間リスト用のデータフィルタリング (ここもJST基準に修正)
            // JSTで月初と月末の範囲を計算
            // monthは0始まり。Date(year, month, 1)はローカル時間だが、簡易的に文字比較部分は下部のループに任せる
            // ここでは簡易的に全データを渡して、表示時に絞る形でも良いが、既存ロジックに合わせて修正
            
            const monthStart = new Date(year, month, 1);
            const monthEnd = new Date(year, month + 1, 0, 23, 59, 59);
            
            // リスト表示用フィルタ（簡易実装: 少し広めに取っておく）
            const monthlyData = allStreams.filter(s => {
                const d = new Date(s.start_time);
                return d >= new Date(monthStart.getTime() - 86400000) && d <= new Date(monthEnd.getTime() + 86400000);
            });
            renderSimpleList(monthlyData, 'monthly-list-tbody'); // 注: リスト側の日時は別途修正が必要ですが、まずはカレンダーを直します

            for(let i=0; i<firstDay; i++) { grid.innerHTML += '<div class="cal-day other-month"></div>'; }

            for(let day=1; day<=daysInMonth; day++) {
                // カレンダーのセルの日付文字列 (YYYY-MM-DD)
                const dateStr = `${year}-${String(month+1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
                
                let eventsHtml = '';
                
                // 【修正箇所】 UTC文字列比較ではなく、JSTに変換してから日付一致を確認する
                const daysStreams = monthlyData.filter(s => {
                    const utcDate = new Date(s.start_time);
                    // UTC時間に9時間を足して、強制的にJSTの時刻を持つDateオブジェクトを作る
                    // (toISOString()した時にJSTの日時文字列が出るようにするハック)
                    const jstDate = new Date(utcDate.getTime() + 9 * 60 * 60 * 1000);
                    const sDateStr = jstDate.toISOString().substring(0, 10); // "2026-01-21" の部分を取得
                    return sDateStr === dateStr;
                });

                daysStreams.forEach(s => {
                    // 【修正箇所】 表示時刻もJSTにする
                    const utcDate = new Date(s.start_time);
                    const jstDate = new Date(utcDate.getTime() + 9 * 60 * 60 * 1000);
                    const timeStr = jstDate.toISOString().substring(11, 16); // "HH:mm" を取得

                    eventsHtml += `<div class="cal-event" onclick="window.location.href='/analytics/stream/${s.sid}'" title="${s.title}">🎥 ${timeStr} ${s.game_name}</div>`;
                });
                grid.innerHTML += `<div class="cal-day"><div class="cal-date-num">${day}</div>${eventsHtml}</div>`;
            }
        }

        // --- 週間タイムライン操作 ---
        function changeTimelineDate(val) {
            if(val) {
                timelineEndDate = new Date(val);
                renderWeeklyTimeline();
            }
        }

        function moveTimeline(days) {
            if (days === 0) {
                timelineEndDate = new Date();
            } else {
                timelineEndDate.setDate(timelineEndDate.getDate() + days);
            }
            const picker = document.getElementById('timelinePicker');
            if(picker) {
                const y = timelineEndDate.getFullYear();
                const m = String(timelineEndDate.getMonth() + 1).padStart(2, '0');
                const d = String(timelineEndDate.getDate()).padStart(2, '0');
                picker.value = `${y}-${m}-${d}`;
            }
            renderWeeklyTimeline();
        }
        
        // --- 週間タイムライン描画 (日付またぎ対応・アニメーション無効) ---
        function renderWeeklyTimeline() {
            const ctx = document.getElementById('weeklyTimelineCanvas').getContext('2d');
            
            const endDate = new Date(timelineEndDate);
            endDate.setHours(23, 59, 59, 999);
            
            const startDate = new Date(timelineEndDate);
            startDate.setDate(startDate.getDate() - 6);
            startDate.setHours(0, 0, 0, 0);
            
            const weeklyData = allStreams.filter(s => {
                const d = new Date(s.start_time);
                return d >= startDate && d <= endDate;
            });

            // 週間リスト描画
            renderSimpleList(weeklyData, 'weekly-list-tbody');

            const labels = [];
            for(let i=0; i<7; i++) {
                const d = new Date(startDate);
                d.setDate(d.getDate() + i);
                labels.push(`${d.getMonth()+1}/${d.getDate()}`);
            }

            const datasets = [{
                label: '配信時間帯',
                data: [],
                backgroundColor: 'rgba(100, 65, 165, 0.6)',
                borderColor: 'rgba(100, 65, 165, 1)',
                borderWidth: 1,
                barPercentage: 0.6
            }];

            weeklyData.forEach(s => {
                const d = new Date(s.start_time);
                const dateStr = `${d.getMonth()+1}/${d.getDate()}`;
                
                const startH = d.getHours() + d.getMinutes()/60;
                let endH = startH;
                
                if (s.duration) {
                    const parts = s.duration.match(/(\d+)h\s*(\d+)m/) || s.duration.match(/(\d+)m/);
                    if (parts) {
                        let durM = 0;
                        if (parts.length === 3) durM = parseInt(parts[1])*60 + parseInt(parts[2]);
                        else durM = parseInt(parts[1]);
                        endH += durM / 60;
                    }
                }

                if (endH > 24) {
                    // 当日分
                    if (labels.includes(dateStr)) {
                        datasets[0].data.push({
                            x: [startH, 24],
                            y: dateStr,
                            title: s.title
                        });
                    }
                    // 翌日分
                    const nextDay = new Date(d);
                    nextDay.setDate(nextDay.getDate() + 1);
                    const nextDateStr = `${nextDay.getMonth()+1}/${nextDay.getDate()}`;
                    
                    if (labels.includes(nextDateStr)) {
                        datasets[0].data.push({
                            x: [0, endH - 24],
                            y: nextDateStr,
                            title: s.title + " (続き)"
                        });
                    }
                } else {
                    if (labels.includes(dateStr)) {
                        datasets[0].data.push({
                            x: [startH, endH],
                            y: dateStr,
                            title: s.title
                        });
                    }
                }
            });

            if (timelineChart) { timelineChart.destroy(); }

            timelineChart = new Chart(ctx, {
                type: 'bar',
                data: { labels: labels, datasets: datasets },
                options: {
                    indexAxis: 'y',
                    animation: false,
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { min: 0, max: 24, title: {display:true, text:'時間 (0-24)'}, ticks: { stepSize: 1 } },
                        y: { title: {display:true, text:'日付'} }
                    },
                    plugins: {
                        tooltip: {
                            callbacks: {
                                label: function(ctx) { return ctx.raw.title || ''; }
                            }
                        }
                    }
                }
            });
        }

        // --- 推移グラフ機能 (強化版) ---
        function renderChart(data, minDate, maxDate) {
            const ctx = document.getElementById('trendChartCanvas').getContext('2d');
            
            // データセット作成
            const pointsViewers = data.map(s => ({ x: s.start_time, y: s.max_viewers, title: s.title, game: s.game_name }));
            // const pointsFollowers = data.map(s => ({ x: s.start_time, y: s.follower_count || null, title: s.title, game: s.game_name }));
            const pointsDurations = data.map(s => {
                if (!s.duration || typeof s.duration !== 'string') return { x: s.start_time, y: 0 };
                const parts = s.duration.match(/(\d+)h\s*(\d+)m/) || s.duration.match(/(\d+)m/);
                let val = 0;
                if (parts) {
                    if (parts.length === 3) val = parseInt(parts[1])*60 + parseInt(parts[2]);
                    else val = parseInt(parts[1]);
                }
                return { x: s.start_time, y: val };
            });

            if (trendChart) { trendChart.destroy(); }

            trendChart = new Chart(ctx, {
                type: 'line',
                data: {
                    datasets: [
                        { label: '最大同接数', data: pointsViewers, borderColor: '#6441a5', backgroundColor: '#6441a5', yAxisID: 'y', tension: 0.1 },
                        // ★修正: フォロワー数データのソースを followerHistory に変更し、見た目を調整
                        { 
                            label: 'フォロワー数', 
                            data: followerHistory, 
                            borderColor: '#e91e63', 
                            backgroundColor: '#e91e63', 
                            yAxisID: 'y1', 
                            tension: 0.1, 
                            pointRadius: 0, // 点を消して線のみにする（データが多いと見づらいため）
                            borderWidth: 2,
                            stepped: true   // 階段状のグラフにする（日次データに適している）
                        },
                        { label: '配信時間(分)', data: pointsDurations, borderColor: '#009688', backgroundColor: 'rgba(0,150,136,0.1)', fill: true, tension: 0.1, yAxisID: 'y2' }
                    ]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    scales: {
                        x: { type: 'time', time: { unit: 'day', tooltipFormat: 'yyyy/MM/dd HH:mm' }, min: minDate.toISOString(), max: maxDate.toISOString(), title: { display: true, text: '日付' } },
                        y: { type: 'linear', display: true, position: 'left', beginAtZero: true, title: {display:true, text:'同接 (人)'} },
                        y1: { type: 'linear', display: true, position: 'right', grid: { drawOnChartArea: false }, title: {display:true, text:'フォロワー (人)'} },
                        y2: { type: 'linear', display: false, position: 'right', grid: { drawOnChartArea: false }, min: 0 } // 配信時間は軸表示なしで背景表示
                    },
                    plugins: {
                        tooltip: {
                            callbacks: {
                                afterLabel: function(context) {
                                    const raw = context.raw;
                                    if(raw.game || raw.title) { return [`🎮 ${raw.game}`, `📝 ${raw.title}`]; }
                                    return [];
                                }
                            }
                        }
                    }
                }
            });
        }
        
        // ★修正: ページネーション付きリスト描画
        function changePage(delta) {
            currentPage += delta;
            renderFilteredList();
        }

        function renderFilteredList() {
            const tbody = document.getElementById('trend-list-tbody');
            const pageInfo = document.getElementById('page-info');
            if(!tbody) return;
            
            // ページ計算
            const totalItems = currentFilteredData.length;
            const totalPages = Math.ceil(totalItems / itemsPerPage);
            
            if (currentPage < 1) currentPage = 1;
            if (currentPage > totalPages && totalPages > 0) currentPage = totalPages;
            
            const start = (currentPage - 1) * itemsPerPage;
            const end = start + itemsPerPage;
            const pageData = currentFilteredData.slice(start, end);

            if (pageData.length === 0) {
                tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:20px; color:#999;">表示範囲内に配信データがありません</td></tr>';
                pageInfo.innerText = "0 / 0 ページ";
                return;
            }
            
            pageInfo.innerText = `${currentPage} / ${totalPages} ページ (${totalItems}件)`;

            tbody.innerHTML = pageData.map(s => `
                <tr>
                    <td><div style="font-weight:bold;">${s.start_time.substring(0, 10)}</div><div style="font-size:0.8em; color:#888;">${s.start_time.substring(11, 16)}</div></td>
                    <td><a href="/analytics/stream/${s.sid}" target="_blank" style="font-weight:bold; color:#6441a5; text-decoration:none;">${s.title}</a></td>
                    <td>${s.game_name}</td>
                    <td><b>${s.max_viewers}</b> / ${s.avg_viewers}</td>
                    <td>${s.follower_count ? s.follower_count + '人' : '<span style="color:#ccc;">-</span>'}</td>
                </tr>
            `).join('');
        }

        // DL進捗の自動更新
        setInterval(function(){
            fetch('/api/download_progress').then(r=>r.json()).then(data => {
                const activeSids = new Set(Object.keys(data));
                let shouldReload = false;
                for (const [sid, info] of Object.entries(data)) {
                    const el = document.getElementById('progress-' + sid);
                    if(el) {
                        if(info.status === 'downloading') {
                            el.innerHTML = `<div class="status-wait">📥 ${info.percent}%</div><div style="font-size:0.7em; color:#666;">(${info.speed})</div>`;
                        } else if(info.status === 'finished') {
                            el.innerHTML = `<div class="status-wait">🔄 仕上げ中...</div>`;
                        } else if(info.status === 'failed') { el.innerHTML = `<span class="status-fail">❌ エラー</span>`; }
                    }
                }
                document.querySelectorAll('[id^="progress-"]').forEach(el => {
                    if (el.querySelector('.status-wait')) {
                        const sid = el.id.replace('progress-', '');
                        if (!activeSids.has(sid)) { shouldReload = true; }
                    }
                });
                if (shouldReload) { location.reload(); }
            });
        }, 2000);

        // 編集モーダルの制御
        function openEditStreamModal(sid, title, game) {
            document.getElementById('edit_stream_id').value = sid;
            document.getElementById('edit_stream_title').value = title;
            document.getElementById('edit_stream_game').value = game;
            document.getElementById('editStreamModal').style.display = 'block';
        }
        function closeEditStreamModal() {
            document.getElementById('editStreamModal').style.display = 'none';
        }
    </script>
</head>
<body>
    <div class="container">
        <div class="header"><h1>📈 配信アナリティクス</h1><a href="/" class="btn btn-back">← ダッシュボードへ戻る</a></div>
        
        {% if view == 'list' %}
            <div class="tab-menu">
                <button id="btn-list" class="tab-btn active" onclick="switchTab('list')">📄 一覧</button>
                <button id="btn-calendar" class="tab-btn" onclick="switchTab('calendar')">📅 カレンダー</button>
                <button id="btn-trends" class="tab-btn" onclick="switchTab('trends')">📊 推移・分析</button>
            </div>

            <div id="list" class="tab-content active">
                <div class="card">
                    <h2><div>📺 過去の配信一覧</div>
                        <div style="display:flex; gap:10px;">
                            <form action="/force_sync_history" method="post" onsubmit="return confirm('Twitch APIから最新情報を取得し、タイトルや時間を更新します。\\n(ゲーム名は現在の設定を維持します)\\nよろしいですか？');">
                                <button class="btn btn-primary" style="font-size:0.8em; background-color: #007bff; border-color: #007bff; opacity: 1;">🔄 情報更新 (API)</button>
                            </form>
                            
                            <form action="/download_all_vods" method="post" onsubmit="return confirm('未保存のアーカイブを全てダウンロードしますか？時間がかかります。');">
                                <button class="btn btn-bulk">📥 一括ダウンロード (未保存のみ)</button>
                            </form>
                        </div>
                    </h2>
                    
                    <input type="text" id="searchInput" onkeyup="filterTable()" placeholder="タイトルやゲーム名で検索..." class="search-box">
                    
                    {% if index_data %}
                    <table id="streamTable">
                        <thead>
                            <tr>
                                <th class="col-date" onclick="sortTable(0)">日時</th>
                                <th class="col-title" onclick="sortTable(1)">タイトル</th>
                                <th class="col-game" onclick="sortTable(2)">ゲーム</th>
                                <th class="col-duration" onclick="sortTable(3)">時間</th>
                                <th class="col-source" onclick="sortTable(4)">ソース</th>
                                <th class="col-viewers" onclick="sortTable(5)">同接<br><span style="font-size:0.8em; font-weight:normal;">(平均/最大)</span></th>
                                <th class="col-archive" onclick="sortTable(6)">アーカイブ</th>
                                <th class="col-action">Action</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for data in sorted_list %}
                            {% set sid = data.sid %}
                            <tr>
                                <td onclick="window.location.href='/analytics/stream/{{ sid }}'" style="cursor:pointer;">
                                    <div class="date-main">{{ data.start_time[:10] }}</div>
                                    <div class="date-sub">{{ data.start_time[11:16] }}</div>
                                </td>
                                <td onclick="window.location.href='/analytics/stream/{{ sid }}'" style="cursor:pointer;">{{ data.title }}</td>
                                <td>{{ data.game_name }}</td>
                                <td>{{ data.duration_short }}</td>
                                <td class="nowrap">
                                    {% if data.source == 'api' %}<span class="source-api">API取得</span>
                                    {% else %}<span class="source-bot">Bot記録</span>{% endif %}
                                </td>
                                <td>{{ data.avg_viewers }} / {{ data.max_viewers }}</td>
                                <td id="progress-{{ sid }}" class="nowrap">
                                    {% if data.vod_status == 'downloaded' %}
                                        {% if data.encode_status == 'encoded' %}
                                            <span class="status-dl" title="エンコード済 ({{ '{:.1f}'.format(data.archive_file_size / 1073741824) }}GB)">✅</span>
                                        {% elif data.encode_status == 'missing' %}
                                            <span style="color:#e65100;" title="ファイルが見つかりません">⚠️</span>
                                        {% else %}
                                            <span style="color:#1565c0;" title="エンコード待ち ({{ '{:.1f}'.format(data.archive_file_size / 1073741824) }}GB)">🕐</span>
                                        {% endif %}
                                    {% elif data.vod_status == 'downloading' %}<div class="status-wait">⏳ DL中...</div>
                                    {% elif data.vod_status == 'failed' %}<span class="status-fail">❌ 失敗</span>
                                    {% else %}<span style="color:#aaa;">- 未保存</span>{% endif %}
                                </td>
                                <td class="nowrap">
                                    <button class="btn btn-edit" onclick="openEditStreamModal('{{ sid }}', '{{ data.title|replace("'", "\\'")|replace('"', '&quot;') }}', '{{ data.game_name|replace("'", "\\'")|replace('"', '&quot;') }}')">✏️</button>
                                    
                                    {% if data.vod_status == 'downloading' %}
                                        <form action="/cancel_download/{{ sid }}" method="post" style="display:inline;">
                                            <button class="btn btn-stop" onclick="return confirm('ダウンロードを中止しますか？')">🛑</button>
                                        </form>
                                    {% elif data.vod_status == 'downloaded' %}
                                        <form action="/delete_vod/{{ sid }}" method="post" style="display:inline;">
                                            <button class="btn btn-del" onclick="return confirm('ファイルを削除し、ステータスをリセットします。再DL可能になりますがよろしいですか？')">🗑️</button>
                                        </form>
                                    {% else %}
                                        <form action="/download_vod/{{ sid }}" method="post" style="display:inline;">
                                            <button class="btn btn-dl">📥</button>
                                        </form>
                                    {% endif %}
                                </td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                    {% else %}<div style="text-align:center; padding:20px; color:#888;">まだ記録された配信データがありません</div>{% endif %}
                </div>
            </div>

            <div id="calendar" class="tab-content">
                <div class="dashboard-grid">
                    <div class="card">
                        <div class="calendar-header">
                            <button class="btn btn-secondary" style="font-weight:bold; color:white;" onclick="changeMonth(-1)">◀ 前月</button>
                            <h2 id="calendarTitle" style="border:none; margin:0;">2026年 1月</h2>
                            <button class="btn btn-secondary" style="font-weight:bold; color:white;" onclick="changeMonth(1)">翌月 ▶</button>
                        </div>
                        <div id="calendarGrid" class="calendar-grid"></div>
                    </div>
                    
                    <div class="card">
                        <h3 style="margin-top:0; border-bottom:1px solid #eee; padding-bottom:5px;">📅 今月の配信リスト</h3>
                        <div style="overflow-y:auto; max-height:400px;">
                            <table>
                                <thead><tr><th>日時</th><th>タイトル</th><th>ゲーム</th><th>時間</th></tr></thead>
                                <tbody id="monthly-list-tbody"></tbody>
                            </table>
                        </div>
                    </div>
                </div>
                
                <hr style="margin: 30px 0; border:0; border-top:1px dashed #ccc;">

                <div class="dashboard-grid">
                    <div class="card">
                        <h2 style="border:none; margin:0; padding-bottom:10px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
                            <span>⏱️ 配信タイムライン (週間)</span>
                            <div style="display:flex; align-items:center; gap:5px;">
                                <button class="btn btn-secondary" onclick="moveTimeline(-7)">◀</button>
                                <input type="date" id="timelinePicker" class="date-input" onchange="changeTimelineDate(this.value)">
                                <button class="btn btn-secondary" onclick="moveTimeline(0)">今週</button>
                                <button class="btn btn-secondary" onclick="moveTimeline(7)">▶</button>
                            </div>
                        </h2>
                        <div style="height: 300px;">
                            <canvas id="weeklyTimelineCanvas"></canvas>
                        </div>
                    </div>

                    <div class="card">
                        <h3 style="margin-top:0; border-bottom:1px solid #eee; padding-bottom:5px;">📑 週間の配信リスト</h3>
                        <div style="overflow-y:auto; max-height:300px;">
                            <table>
                                <thead><tr><th>日時</th><th>タイトル</th><th>ゲーム</th><th>時間</th></tr></thead>
                                <tbody id="weekly-list-tbody"></tbody>
                            </table>
                        </div>
                    </div>
                </div>
            </div>

            <div id="trends" class="tab-content">
                <div class="card">
                    <h2>📊 配信メトリクス推移</h2>
                    
                    <div class="chart-controls">
                        <button class="range-btn" id="btn-range-1W" onclick="setRange('1W')">最近1週間</button>
                        <button class="range-btn" id="btn-range-1M" onclick="setRange('1M')">最近1ヶ月</button>
                        <button class="range-btn" id="btn-range-1Y" onclick="setRange('1Y')">最近1年</button>
                        <button class="range-btn" id="btn-range-ALL" onclick="setRange('ALL')">全期間</button>
                        <span style="margin:0 10px; color:#ccc;">|</span>
                        <input type="date" id="start-date" class="date-input" onchange="setRange('CUSTOM')">
                        <span style="font-weight:bold;">~</span>
                        <input type="date" id="end-date" class="date-input" onchange="setRange('CUSTOM')">
                    </div>

                    <div class="chart-container">
                        <canvas id="trendChartCanvas"></canvas>
                    </div>
                </div>
                
                <div class="dashboard-grid" style="display:grid; grid-template-columns: 1fr 1fr; gap:20px; margin-bottom:20px;">
                    <div class="card">
                        <h2>📈 期間内サマリー <span style="font-size:0.6em; color:#888; font-weight:normal;">※Bot計測分のみ</span></h2>
                        <div class="stat-grid">
                            <div class="stat-box"><div class="stat-val" id="stat-avg-viewers">-</div><div class="stat-label">平均同接</div></div>
                            <div class="stat-box"><div class="stat-val" id="stat-total-time">-</div><div class="stat-label">総配信時間</div></div>
                            <div class="stat-box"><div class="stat-val" id="stat-stream-count">-</div><div class="stat-label">配信回数</div></div>
                        </div>
                    </div>
                    <div class="card">
                        <h2>🎮 ゲーム別集計</h2>
                        <div style="overflow-y:auto; max-height:200px;">
                            <table>
                                <thead><tr><th>ゲーム</th><th>平均同接</th><th>合計時間</th><th>回数</th></tr></thead>
                                <tbody id="gamestats-tbody"></tbody>
                            </table>
                        </div>
                    </div>
                </div>

                <div class="card">
                    <h2>📑 表示範囲内の配信一覧</h2>
                    <table>
                        <thead>
                            <tr>
                                <th class="col-date">日時</th>
                                <th class="col-title">タイトル</th>
                                <th class="col-game">ゲーム</th>
                                <th class="col-viewers">最大同接 / 平均</th>
                                <th class="col-follower">フォロワー</th>
                            </tr>
                        </thead>
                        <tbody id="trend-list-tbody">
                            </tbody>
                    </table>
                    <div class="pagination">
                        <button class="btn btn-secondary" onclick="changePage(-1)">◀ 前へ</button>
                        <span id="page-info" class="page-info"></span>
                        <button class="btn btn-secondary" onclick="changePage(1)">次へ ▶</button>
                    </div>
                </div>
            </div>

        {% elif view == 'detail' %}
            <div class="card">
                <h2>
                    <div>📊 {{ stream_info.title }} 
                    <span style="font-size:0.6em; color:#666;">({{ stream_info.start_time | format_date }})</span>
                    {% if stream_info.source == 'api' %}<span class="source-api" style="font-size:0.6em; margin-left:10px;">API取得データ</span>{% endif %}
                    </div>
                    <a href="/analytics" class="btn btn-back" style="font-size:0.8em;">← 一覧へ戻る</a>
                </h2>
                
                {% if not has_log_file %}
                    <div style="padding:20px; text-align:center; color:#666; background:#f9f9f9; border-radius:8px;">
                        <p>⚠️ この配信はBot稼働外のため、詳細なログ（グラフ・チャット）はありません。</p>
                        {% if stream_info.thumbnail_url %}
                        <img src="{{ stream_info.thumbnail_url.replace('%{width}', '640').replace('%{height}', '360') }}" style="max-width:100%; border-radius:8px; margin-top:10px;">
                        {% endif %}
                        <div style="margin-top:15px; font-weight:bold;">
                            総視聴回数: {{ stream_info.view_count }} 回<br>
                            配信時間: {{ stream_info.duration }}
                        </div>
                    </div>
                {% else %}
                    <div class="stat-grid">
                        <div class="stat-box"><div class="stat-val">{{ stats.max_viewers }}</div><div class="stat-label">最大同接</div></div>
                        <div class="stat-box"><div class="stat-val">{{ stats.avg_viewers }}</div><div class="stat-label">平均同接</div></div>
                        <div class="stat-box"><div class="stat-val">{{ stats.total_comments }}</div><div class="stat-label">総コメント数</div></div>
                        <div class="stat-box"><div class="stat-val" style="color:#e91e63;">{{ stats.total_subs }}</div><div class="stat-label">新規サブスク</div></div>
                        <div class="stat-box"><div class="stat-val" style="color:#ff9800;">{{ stats.total_bits }}</div><div class="stat-label">ビッツ総額</div></div>
                        <div class="stat-box"><div class="stat-val" style="color:#009688;">{{ stats.total_points }}</div><div class="stat-label">ポイント使用</div></div>
                    </div>
                    <div class="chart-container"><canvas id="viewerChart"></canvas></div>
                {% endif %}
            </div>
            
            {% if has_log_file %}
            <div class="dashboard-grid" style="display:grid; grid-template-columns: 1fr 1fr; gap:20px;">
                <div class="card">
                    <h2>💬 チャット & イベントログ</h2>
                    <div class="chat-log">
                        {% for line in chat_logs %}
                            {% if line.type == 'msg' %}<div class="chat-line"><span class="ts">[{{ line.time }}]</span><span class="usr {% if line.is_sub %}sub{% endif %}">{{ line.user }}:</span><span>{{ line.text }}</span></div>
                            {% elif line.type == 'point' %}<div class="chat-line"><span class="ts">[{{ line.time }}]</span><span class="pt">🎨 {{ line.user }} がポイント使用: {{ line.reward_id }}</span><span style="color:#888;">{{ line.text }}</span></div>{% endif %}
                        {% endfor %}
                    </div>
                </div>
                <div class="card"><h2>✨ スタンプ使用率 TOP5</h2><table>{% for k, v in top_emotes %}<tr><td><img src="https://static-cdn.jtvnw.net/emoticons/v2/{{ k }}/default/dark/1.0" style="vertical-align:middle; margin-right:5px; width:32px; height:32px;"> <span style="font-size:0.8em; color:#666;">ID: {{ k }}</span></td><td style="text-align:right; font-weight:bold;">{{ v }}回</td></tr>{% endfor %}</table></div>
            </div>
            <script>
                const ctx = document.getElementById('viewerChart').getContext('2d');
                new Chart(ctx, { type: 'line', data: { labels: {{ chart_labels | tojson }}, datasets: [{ label: '視聴者数', data: {{ chart_viewers | tojson }}, borderColor: 'rgb(75, 192, 192)', tension: 0.1, yAxisID: 'y' }, { label: 'コメント数/分', data: {{ chart_comments | tojson }}, borderColor: 'rgb(255, 99, 132)', backgroundColor: 'rgba(255, 99, 132, 0.2)', type: 'bar', yAxisID: 'y1' }] }, options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'index', intersect: false }, scales: { y: { type: 'linear', display: true, position: 'left', beginAtZero: true }, y1: { type: 'linear', display: true, position: 'right', beginAtZero: true, grid: { drawOnChartArea: false } } } } });
            </script>
            {% endif %}
        {% endif %}
    </div>

    <div id="editStreamModal" class="modal">
        <div class="modal-content">
            <span class="close" onclick="closeEditStreamModal()">&times;</span>
            <h2 style="border:none;">✏️ 配信情報の編集</h2>
            <form action="/update_stream_info" method="post">
                <input type="hidden" id="edit_stream_id" name="stream_id">
                <div class="form-group">
                    <label>配信タイトル</label>
                    <input type="text" id="edit_stream_title" name="title" required>
                </div>
                <div class="form-group">
                    <label>ゲームカテゴリー</label>
                    <input type="text" id="edit_stream_game" name="game" required list="existing_games">
                </div>
                <div style="margin-top:20px; text-align:right;">
                    <button class="btn btn-primary">保存する</button>
                </div>
            </form>
        </div>
    </div>
</body>
</html>
"""