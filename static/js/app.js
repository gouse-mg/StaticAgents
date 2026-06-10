const SEV_ORDER = { info: 0, low: 1, medium: 2, high: 3, critical: 4 };
const SEV_COLOR = {
    critical: '#ef4444', high: '#f97316',
    medium: '#f59e0b', low: '#38bdf8', info: '#94a3b8'
};

class TimelineStepper {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.nodes = {}; // agentName -> { element, logContainer, indicator, status }
        this.lastAgent = null;
    }

    getIcon(agent) {
        if (agent.includes('Analyst')) return '🧠';
        if (agent.includes('Executor')) return '⚙️';
        if (agent.includes('Solver')) return '🛡️';
        return '🤖';
    }

    createNode(agent) {
        const item = document.createElement('div');
        item.className = 'stepper-item';
        
        const nodeContainer = document.createElement('div');
        nodeContainer.className = 'node-container';
        
        const indicator = document.createElement('div');
        indicator.className = 'node-indicator active loading';
        indicator.innerHTML = this.getIcon(agent);
        
        nodeContainer.appendChild(indicator);
        
        const content = document.createElement('div');
        content.className = 'node-content';
        
        const title = document.createElement('div');
        title.className = 'node-title';
        title.textContent = agent;
        
        const logs = document.createElement('div');
        logs.className = 'node-logs';
        
        content.appendChild(title);
        content.appendChild(logs);
        
        item.appendChild(nodeContainer);
        item.appendChild(content);
        
        this.container.appendChild(item);
        
        return {
            element: item,
            indicator: indicator,
            logContainer: logs,
            status: 'active'
        };
    }

    addLog(line) {
        // Detect agent
        let agent = 'System';
        if (line.includes('[Analyst]')) agent = 'Analyst Agent';
        else if (line.includes('Executor')) agent = 'Executor Agent';
        else if (line.includes('Solver')) agent = 'Solver Agent';
        else if (line.includes('[CRITICAL]') || line.includes('[HIGH]') || line.includes('[MEDIUM]') || line.includes('[LOW]')) agent = 'Analyst Agent';

        // Check if we need a new node or append to last
        // If agent changed or it's a new cycle, we might want a new node.
        // For simplicity, let's create a new node if the agent type changes from the last one.
        if (agent !== this.lastAgent) {
            // Mark previous as complete if it was active
            if (this.lastAgent && this.nodes[this.lastAgent]) {
                // We don't mark as complete here because we wait for "200 OK"
            }
            
            // Create or get node
            // To allow multiple nodes of same type (e.g. Executor runs twice), we use a unique ID
            const nodeId = agent + '_' + Date.now(); 
            this.nodes[nodeId] = this.createNode(agent);
            this.lastNodeId = nodeId;
            this.lastAgent = agent;
        }

        const node = this.nodes[this.lastNodeId];
        if (node) {
            const logLine = document.createElement('div');
            logLine.className = 'tl-line';
            logLine.textContent = '> ' + line;
            node.logContainer.appendChild(logLine);

            // Success check
            if (line.includes('200 OK')) {
                node.indicator.classList.remove('loading');
                node.indicator.classList.add('complete');
                node.indicator.innerHTML = '<svg class="check-icon" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"></path></svg>';
            }
            
            // Error check
            if (line.includes('ERROR')) {
                node.indicator.classList.remove('loading');
                node.indicator.style.borderColor = 'var(--critical)';
                node.indicator.style.color = 'var(--critical)';
                node.indicator.innerHTML = '❌';
            }
        }
        
        this.container.parentElement.scrollTop = this.container.parentElement.scrollHeight;
    }
}

let stepper = null;

function renderFindings(findings) {
    if (!findings.length) return;
    const el = document.getElementById('findings');
    el.innerHTML = findings.map(f => `
        <div class="finding-card ${f.severity}">
            <div class="finding-top">
                <span class="severity-badge badge-${f.severity}">${f.severity}</span>
                <span class="finding-title">${f.title}</span>
            </div>
            <p class="finding-summary">${f.summary}</p>
            <div class="indicators">
                ${(f.indicators || []).map(i =>
                    `<span class="indicator">${i}</span>`
                ).join('')}
            </div>
        </div>
    `).join('');

    // Update threat meter
    const maxSev = findings.reduce((acc, f) =>
        Math.max(acc, SEV_ORDER[f.severity] || 0), 0);
    const pct = [0, 20, 45, 70, 100][maxSev];
    const color = Object.values(SEV_COLOR)[maxSev];
    const label = Object.keys(SEV_ORDER)[maxSev].toUpperCase();
    document.getElementById('bar-fill').style.width = pct + '%';
    document.getElementById('bar-fill').style.background = color;
    document.getElementById('threat-score').textContent = label;
    document.getElementById('threat-score').style.color = color;
}

let processedLogs = 0;

function poll(runId) {
    fetch(`/status/${runId}`)
        .then(r => r.json())
        .then(data => {
            // Initialize stepper if not already
            if (!stepper) {
                const tl = document.getElementById('timeline');
                tl.innerHTML = '<div class="vertical-stepper" id="stepper-root"></div>';
                stepper = new TimelineStepper('stepper-root');
            }

            // Process new logs
            if (data.logs && data.logs.length > processedLogs) {
                for (let i = processedLogs; i < data.logs.length; i++) {
                    stepper.addLog(data.logs[i]);
                }
                processedLogs = data.logs.length;
            }

            // Render findings
            if (data.findings && data.findings.length) {
                renderFindings(data.findings);
            }

            // Update status dot
            const stopBtn = document.getElementById('stop-btn');
            if (data.status === 'done' || data.status === 'error' || data.status === 'stopped') {
                document.getElementById('dot').classList.add('done');
                if (stopBtn) stopBtn.style.display = 'none';
                if (data.status === 'stopped') {
                    processedLogs = -1; // stop processing
                }
            } else {
                if (stopBtn) stopBtn.style.display = 'block';
                setTimeout(() => poll(runId), 1500);
            }
        });
}

// Global initialization
window.addEventListener('DOMContentLoaded', () => {
    if (typeof runId !== 'undefined' && runId) {
        poll(runId);
        
        const stopBtn = document.getElementById('stop-btn');
        if (stopBtn) {
            stopBtn.addEventListener('click', () => {
                if (confirm("Stop the analysis pipeline?")) {
                    fetch(`/stop/${runId}`, { method: 'POST' })
                        .then(r => r.json())
                        .then(res => {
                            if (res.status === 'success') {
                                stopBtn.style.display = 'none';
                            }
                        });
                }
            });
        }
    }
});
