// ============================================================
// Layout management
// ============================================================
function setLayout(mode) {
    document.body.classList.remove('layout-a', 'layout-b');
    document.body.classList.add('layout-' + mode);
    localStorage.setItem('layout', mode);
    var btn = document.getElementById('layout-toggle');
    if (btn) btn.textContent = mode.toUpperCase();
    if (mode === 'a') {
        if (typeof initSortable === 'function') initSortable();
    } else {
        if (typeof initLayoutB === 'function') initLayoutB();
    }
}

function toggleLayout() {
    var cur = localStorage.getItem('layout') || 'b';
    setLayout(cur === 'a' ? 'b' : 'a');
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

// Keyboard: Escape closes modals
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        document.querySelectorAll('.modal').forEach(function(modal) {
            if (modal.style.display === 'block') modal.style.display = 'none';
        });
    }
});
