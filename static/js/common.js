// ============================================================
// Layout management (B = 管理モード, M = モニターモード)
// ============================================================
function setLayout(mode) {
    document.body.classList.remove('layout-a', 'layout-b', 'layout-m');
    document.body.classList.add('layout-' + mode);
    localStorage.setItem('layout', mode);
    var btn = document.getElementById('layout-toggle');
    if (btn) {
        if (mode === 'b') {
            btn.textContent = '📡';
            btn.title = 'モニターモードへ切り替え';
        } else {
            btn.textContent = '📊';
            btn.title = '管理モードへ切り替え';
        }
    }
    if (mode === 'b') {
        if (typeof initLayoutB === 'function') initLayoutB();
    } else if (mode === 'm') {
        if (typeof initLayoutM === 'function') initLayoutM();
    }
}

function toggleLayout() {
    var cur = localStorage.getItem('layout') || 'b';
    setLayout(cur === 'b' ? 'm' : 'b');
}

// Center tab switching (layout-b)
function switchCenterTab(name) {
    document.querySelectorAll('#center-group .card[data-card]').forEach(function(c) {
        c.classList.remove('tab-active');
    });
    var target = document.querySelector('#center-group .card[data-card="' + name + '"]');
    if (target) target.classList.add('tab-active');
    document.querySelectorAll('#center-tabs button').forEach(function(b) {
        b.classList.toggle('active', b.dataset.tab === name);
    });
}

function initLayoutB() {
    // Default to presets tab
    switchCenterTab('presets');
}

// Restore layout on load
document.addEventListener('DOMContentLoaded', function() {
    var saved = localStorage.getItem('layout') || 'b';
    // Migrate old 'a' value to 'b'
    if (saved === 'a') saved = 'b';
    setLayout(saved);
});

// ============================================================
// Toast notifications
// ============================================================
function showToast(message, type) {
    type = type || 'success';
    var container = document.getElementById('toast-container');
    if (!container) return;
    var toast = document.createElement('div');
    toast.className = 'toast toast-' + type;
    toast.textContent = message;
    container.appendChild(toast);
    setTimeout(function() {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s';
        setTimeout(function() { toast.remove(); }, 300);
    }, 3000);
}

// ============================================================
// Panel resize (drag handles for layout-b)
// ============================================================
function initResize() {
    var grid = document.getElementById('main-grid');
    if (!grid) return;

    // Restore saved widths for both modes
    var savedLeft  = localStorage.getItem('col-left');
    var savedRight = localStorage.getItem('col-right');
    if (savedLeft)  grid.style.setProperty('--col-left',  savedLeft  + 'px');
    if (savedRight) grid.style.setProperty('--col-right', savedRight + 'px');
    var savedLeftM  = localStorage.getItem('col-left-m');
    var savedRightM = localStorage.getItem('col-right-m');
    if (savedLeftM)  grid.style.setProperty('--col-left-m',  savedLeftM  + 'px');
    if (savedRightM) grid.style.setProperty('--col-right-m', savedRightM + 'px');

    function makeResizer(handleId, getVarAndKey, getNewWidth) {
        var handle = document.getElementById(handleId);
        if (!handle) return;
        handle.addEventListener('mousedown', function(e) {
            e.preventDefault();
            handle.classList.add('dragging');
            var vk = getVarAndKey();
            var startX = e.clientX;
            var startWidth = parseInt(
                getComputedStyle(grid).getPropertyValue(vk.varName) || '0', 10
            );
            function onMove(ev) {
                var newWidth = getNewWidth(startWidth, ev.clientX - startX);
                newWidth = Math.max(180, Math.min(600, newWidth));
                grid.style.setProperty(vk.varName, newWidth + 'px');
                localStorage.setItem(vk.storageKey, newWidth);
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

    function isMonitorMode() {
        return document.body.classList.contains('layout-m');
    }

    makeResizer('resize-left', function() {
        return isMonitorMode()
            ? { varName: '--col-left-m', storageKey: 'col-left-m' }
            : { varName: '--col-left', storageKey: 'col-left' };
    }, function(sw, dx) { return sw + dx; });

    makeResizer('resize-right', function() {
        return isMonitorMode()
            ? { varName: '--col-right-m', storageKey: 'col-right-m' }
            : { varName: '--col-right', storageKey: 'col-right' };
    }, function(sw, dx) { return sw - dx; });
}

document.addEventListener('DOMContentLoaded', initResize);

// Keyboard: Escape closes modals
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal').forEach(function(modal) {
            if (modal.style.display === 'block') modal.style.display = 'none';
        });
    }
});
