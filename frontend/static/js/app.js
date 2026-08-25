/**
 * Steel Estimator - Frontend Application
 */

document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    initUpload();
    initManualInput();
    initDescribe();
});

// ===== Tab Navigation =====
function initTabs() {
    const tabs = document.querySelectorAll('.tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const target = tab.dataset.tab;
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            document.getElementById(`${target}-tab-content`).classList.add('active');
        });
    });
}

// ===== File Upload =====
function initUpload() {
    const dropZone = document.getElementById('drop-zone');
    const fileInput = document.getElementById('file-input');

    dropZone.addEventListener('click', () => fileInput.click());

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        dropZone.classList.add('drag-over');
    });

    dropZone.addEventListener('dragleave', () => {
        dropZone.classList.remove('drag-over');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        dropZone.classList.remove('drag-over');
        const file = e.dataTransfer.files[0];
        if (file) uploadFile(file);
    });

    fileInput.addEventListener('change', () => {
        const file = fileInput.files[0];
        if (file) uploadFile(file);
    });
}

async function uploadFile(file) {
    const ext = file.name.split('.').pop().toLowerCase();
    if (!['dwg', 'dxf', 'pdf'].includes(ext)) {
        showStatus('Supported formats: .dwg, .dxf, .pdf', 'error');
        return;
    }

    const useLLM = document.getElementById('use-llm').checked;
    showLoading(true);

    const formData = new FormData();
    formData.append('file', file);

    const url = `/api/upload-and-estimate?use_llm=${useLLM}`;

    try {
        const response = await fetch(url, { method: 'POST', body: formData });
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.detail || 'Upload failed');
        }

        if (data.estimate) {
            showResults(data.estimate, data.parse_result);
            showStatus(data.message, 'success');
        } else {
            // Show helpful guidance instead of raw parse data
            showDWGGuidance(data.message, data.parse_result);
        }
    } catch (err) {
        showStatus(`Error: ${err.message}`, 'error');
    } finally {
        showLoading(false);
    }
}

// ===== Manual Input =====
let elementCounter = 0;

function initManualInput() {
    document.getElementById('add-element-btn').addEventListener('click', addElementCard);
    document.getElementById('calculate-btn').addEventListener('click', calculateManual);
    addElementCard();
}

function addElementCard() {
    elementCounter++;
    const container = document.getElementById('elements-list');

    const card = document.createElement('div');
    card.className = 'element-card';
    card.id = `element-${elementCounter}`;
    card.innerHTML = `
        <div class="element-card-header">
            <h4>Element #${elementCounter}</h4>
            <button class="btn-remove" onclick="removeElement(${elementCounter})">
                <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2">
                    <path d="M18 6L6 18M6 6l12 12"/>
                </svg>
            </button>
        </div>
        <div class="element-form-grid">
            <div class="form-group">
                <label>Type</label>
                <select class="elem-type">
                    <option value="column">Column</option>
                    <option value="beam">Beam</option>
                    <option value="slab">Slab</option>
                    <option value="footing">Footing</option>
                    <option value="staircase">Staircase</option>
                    <option value="lintel">Lintel</option>
                </select>
            </div>
            <div class="form-group">
                <label>Width (mm)</label>
                <input type="number" class="elem-width" placeholder="300" value="300">
            </div>
            <div class="form-group">
                <label>Depth (mm)</label>
                <input type="number" class="elem-depth" placeholder="450" value="450">
            </div>
            <div class="form-group">
                <label>Length/Span (mm)</label>
                <input type="number" class="elem-length" placeholder="3000" value="3000">
            </div>
            <div class="form-group">
                <label>Main Bar Dia (mm)</label>
                <input type="number" class="elem-main-dia" placeholder="16">
            </div>
            <div class="form-group">
                <label>Main Bar Count</label>
                <input type="number" class="elem-main-count" placeholder="4">
            </div>
            <div class="form-group">
                <label>Stirrup Dia (mm)</label>
                <input type="number" class="elem-stirrup-dia" placeholder="8">
            </div>
            <div class="form-group">
                <label>Stirrup Spacing (mm)</label>
                <input type="number" class="elem-stirrup-spacing" placeholder="150">
            </div>
            <div class="form-group">
                <label>Quantity</label>
                <input type="number" class="elem-quantity" placeholder="1" value="1" min="1">
            </div>
            <div class="form-group">
                <label>Clear Cover (mm)</label>
                <input type="number" class="elem-cover" placeholder="25" value="25">
            </div>
        </div>
    `;
    container.appendChild(card);
}

function removeElement(id) {
    const card = document.getElementById(`element-${id}`);
    if (card) card.remove();
}

async function calculateManual() {
    const cards = document.querySelectorAll('.element-card');
    if (cards.length === 0) {
        showStatus('Add at least one element', 'error');
        return;
    }

    const elements = [];
    cards.forEach(card => {
        const getValue = (cls) => {
            const el = card.querySelector(`.${cls}`);
            return el ? el.value : '';
        };

        elements.push({
            element_type: getValue('elem-type'),
            label: '',
            width: parseFloat(getValue('elem-width')) || 300,
            depth: parseFloat(getValue('elem-depth')) || 300,
            length: parseFloat(getValue('elem-length')) || 3000,
            main_bar_dia: parseFloat(getValue('elem-main-dia')) || null,
            main_bar_count: parseInt(getValue('elem-main-count')) || null,
            stirrup_dia: parseFloat(getValue('elem-stirrup-dia')) || null,
            stirrup_spacing: parseFloat(getValue('elem-stirrup-spacing')) || null,
            quantity: parseInt(getValue('elem-quantity')) || 1,
            clear_cover: parseFloat(getValue('elem-cover')) || 25,
        });
    });

    const projectName = document.getElementById('project-name').value || 'Manual Estimate';

    showLoading(true);

    try {
        const response = await fetch('/api/manual-estimate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ project_name: projectName, elements }),
        });

        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Estimation failed');

        showResults(data);
    } catch (err) {
        showStatus(`Error: ${err.message}`, 'error');
    } finally {
        showLoading(false);
    }
}

// ===== Results Display =====
function showResults(estimate, parseResult) {
    const container = document.getElementById('results-container');
    const resultsTab = document.getElementById('results-tab');
    resultsTab.style.display = 'flex';

    // Store estimate globally for download
    window._lastEstimate = estimate;

    let html = `
        <div class="result-card">
            <h3>Project Summary: ${estimate.project_name}</h3>
            <div class="summary-grid">
                <div class="summary-item">
                    <div class="value">${estimate.total_steel_kg.toFixed(1)}</div>
                    <div class="label">Total Steel (kg)</div>
                </div>
                <div class="summary-item">
                    <div class="value">${estimate.total_steel_tons.toFixed(3)}</div>
                    <div class="label">Total Steel (tonnes)</div>
                </div>
                <div class="summary-item">
                    <div class="value">${estimate.elements.length}</div>
                    <div class="label">Elements Estimated</div>
                </div>
            </div>
            <div style="margin-top: 16px; display: flex; gap: 10px; flex-wrap: wrap;">
                <button onclick="downloadEstimate('csv')" class="download-btn" style="background: #059669; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 0.85rem; display: flex; align-items: center; gap: 6px;">
                    <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
                    Download CSV
                </button>
                <button onclick="downloadEstimate('excel')" class="download-btn" style="background: #1d6f42; color: white; border: none; padding: 8px 16px; border-radius: 6px; cursor: pointer; font-size: 0.85rem; display: flex; align-items: center; gap: 6px;">
                    <svg width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4M7 10l5 5 5-5M12 15V3"/></svg>
                    Download Excel
                </button>
            </div>
        </div>
    `;

    // Bar chart by element type
    const typeData = estimate.summary_by_type;
    const maxType = Math.max(...Object.values(typeData));
    html += `
        <div class="result-card">
            <h3>Steel by Element Type</h3>
            <div class="chart-bar-container">
                ${Object.entries(typeData).map(([type, weight]) => `
                    <div class="chart-bar-row">
                        <span class="chart-bar-label">${type}</span>
                        <div class="chart-bar-track">
                            <div class="chart-bar-fill" style="width: ${(weight/maxType*100).toFixed(1)}%">
                                ${weight > maxType * 0.15 ? weight.toFixed(1) + ' kg' : ''}
                            </div>
                        </div>
                        <span class="chart-bar-value">${weight.toFixed(1)} kg</span>
                    </div>
                `).join('')}
            </div>
        </div>
    `;

    // Bar chart by diameter
    const diaData = estimate.summary_by_diameter;
    const maxDia = Math.max(...Object.values(diaData));
    html += `
        <div class="result-card">
            <h3>Steel by Bar Diameter</h3>
            <div class="chart-bar-container">
                ${Object.entries(diaData).map(([dia, weight]) => `
                    <div class="chart-bar-row">
                        <span class="chart-bar-label">${dia}</span>
                        <div class="chart-bar-track">
                            <div class="chart-bar-fill" style="width: ${(weight/maxDia*100).toFixed(1)}%; background: #6366f1">
                                ${weight > maxDia * 0.15 ? weight.toFixed(1) + ' kg' : ''}
                            </div>
                        </div>
                        <span class="chart-bar-value">${weight.toFixed(1)} kg</span>
                    </div>
                `).join('')}
            </div>
        </div>
    `;

    // Detailed table
    html += `
        <div class="result-card">
            <h3>Detailed Breakdown</h3>
            <div style="overflow-x: auto;">
                <table class="data-table">
                    <thead>
                        <tr>
                            <th>Element</th>
                            <th>Type</th>
                            <th>Dimensions (mm)</th>
                            <th>Bar Type</th>
                            <th class="number">Dia (mm)</th>
                            <th class="number">Count</th>
                            <th class="number">Length (m)</th>
                            <th class="number">Weight (kg)</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${estimate.elements.map(el => 
                            el.rebars.map((bar, i) => `
                                <tr>
                                    ${i === 0 ? `
                                        <td rowspan="${el.rebars.length}">${el.element.label}</td>
                                        <td rowspan="${el.rebars.length}">${el.element.element_type}</td>
                                        <td rowspan="${el.rebars.length}">${el.element.width}×${el.element.depth}×${el.element.length}</td>
                                    ` : ''}
                                    <td>${bar.bar_type}</td>
                                    <td class="number">${bar.diameter}</td>
                                    <td class="number">${bar.count}</td>
                                    <td class="number">${bar.length.toFixed(2)}</td>
                                    <td class="number">${bar.total_weight.toFixed(2)}</td>
                                </tr>
                            `).join('')
                        ).join('')}
                    </tbody>
                    <tfoot>
                        <tr>
                            <td colspan="7" style="text-align:right; font-weight:bold">Total:</td>
                            <td class="number" style="font-weight:bold">${estimate.total_steel_kg.toFixed(2)} kg</td>
                        </tr>
                    </tfoot>
                </table>
            </div>
        </div>
    `;

    container.innerHTML = html;

    // Switch to results tab
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    resultsTab.classList.add('active');
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.getElementById('results-tab-content').classList.add('active');
}

function showParseInfo(parseResult) {
    const container = document.getElementById('results-container');
    const resultsTab = document.getElementById('results-tab');
    resultsTab.style.display = 'flex';

    container.innerHTML = `
        <div class="result-card">
            <h3>File Parsed: ${parseResult.filename}</h3>
            <p>Layers found: ${parseResult.layers.length}</p>
            <p>Elements detected: ${parseResult.element_count}</p>
            <h4 style="margin-top:16px">Layers:</h4>
            <p style="font-size:0.85rem; color: var(--text-secondary)">${parseResult.layers.join(', ')}</p>
            ${parseResult.raw_text_annotations.length > 0 ? `
                <h4 style="margin-top:16px">Text Annotations Found:</h4>
                <ul style="font-size:0.85rem; max-height:200px; overflow-y:auto">
                    ${parseResult.raw_text_annotations.slice(0, 30).map(t => `<li>${t}</li>`).join('')}
                </ul>
            ` : ''}
        </div>
    `;

    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    resultsTab.classList.add('active');
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.getElementById('results-tab-content').classList.add('active');
}

function showDWGGuidance(message, parseResult) {
    const container = document.getElementById('results-container');
    const resultsTab = document.getElementById('results-tab');
    resultsTab.style.display = 'flex';

    const isBinaryFallback = parseResult && parseResult.metadata && parseResult.metadata.parse_method === 'binary_fallback';
    const isPdf = parseResult && parseResult.metadata && parseResult.metadata.parse_method === 'pdf_vision';

    let html = '';
    if (isBinaryFallback) {
        html = `
            <div class="result-card" style="border-left: 4px solid var(--warning)">
                <h3 style="color: var(--warning)">DWG File Cannot Be Decoded Directly</h3>
                <p style="margin: 12px 0; line-height: 1.7">
                    DWG is a proprietary binary format that requires specialized tools to read.
                    The file was uploaded successfully but its structural data couldn't be extracted.
                </p>
                <h4 style="margin-top: 20px">Recommended Solutions:</h4>
                <div style="margin-top: 12px; display: flex; flex-direction: column; gap: 12px;">
                    <div style="background: var(--bg); padding: 16px; border-radius: 8px; border: 1px solid var(--border)">
                        <strong style="color: var(--success)">Option 1: Export as PDF (Best)</strong>
                        <p style="font-size: 0.85rem; margin-top: 4px; color: var(--text-secondary)">
                            Open in AutoCAD/GStarCAD → Print/Export to PDF → Upload the PDF here.
                            Our AI vision will analyze the drawing visually including line weights.
                        </p>
                    </div>
                    <div style="background: var(--bg); padding: 16px; border-radius: 8px; border: 1px solid var(--border)">
                        <strong>Option 2: Save as DXF</strong>
                        <p style="font-size: 0.85rem; margin-top: 4px; color: var(--text-secondary)">
                            Open in AutoCAD/GStarCAD → File → Save As → Choose DXF format → Upload the .dxf file.
                        </p>
                    </div>
                    <div style="background: var(--bg); padding: 16px; border-radius: 8px; border: 1px solid var(--border)">
                        <strong>Option 3: Describe Your Building</strong>
                        <p style="font-size: 0.85rem; margin-top: 4px; color: var(--text-secondary)">
                            Use the "AI Describe" tab to describe your building layout from the drawing.
                        </p>
                    </div>
                </div>
            </div>
        `;
    } else {
        // PDF or other file type that failed
        html = `
            <div class="result-card" style="border-left: 4px solid var(--danger)">
                <h3 style="color: var(--danger)">Analysis Failed</h3>
                <p style="margin: 12px 0; padding: 12px; background: #fef2f2; border-radius: 8px; font-family: monospace; font-size: 0.85rem; word-break: break-word;">
                    ${message || 'Unknown error'}
                </p>
                <h4 style="margin-top: 16px">Troubleshooting:</h4>
                <ul style="margin-top: 8px; padding-left: 20px; font-size: 0.9rem; line-height: 2;">
                    <li>Check that <code>LLM_API_KEY</code> is set correctly in your <code>.env</code> file</li>
                    <li>Ensure <code>LLM_API_BASE</code> points to a valid endpoint (e.g., <code>https://api.openai.com/v1</code>)</li>
                    <li>Ensure <code>LLM_MODEL</code> supports vision/images (e.g., <code>gpt-4o</code>, <code>gpt-4o-mini</code>)</li>
                    <li>Check that your API key has sufficient credits/quota</li>
                    <li>Restart the app after editing .env: <code>python run.py</code></li>
                </ul>
            </div>
        `;
    }

    container.innerHTML = html;

    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    resultsTab.classList.add('active');
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.getElementById('results-tab-content').classList.add('active');
}

// ===== Utilities =====
function showLoading(show) {
    document.getElementById('loading').style.display = show ? 'flex' : 'none';
}

function showStatus(message, type) {
    const el = document.getElementById('upload-status');
    el.textContent = message;
    el.className = `status-message ${type}`;
    el.style.display = 'block';
    setTimeout(() => { el.style.display = 'none'; }, 8000);
}

// ===== AI Describe Building =====
function initDescribe() {
    document.getElementById('describe-btn').addEventListener('click', describeBuilding);
}

async function describeBuilding() {
    const description = document.getElementById('building-description').value.trim();
    if (!description) {
        alert('Please describe your building first');
        return;
    }

    const projectName = document.getElementById('describe-project-name').value || 'AI Estimated Building';

    showLoading(true);
    document.querySelector('.loading-overlay p').textContent = 'AI is analyzing your building description...';

    try {
        const response = await fetch('/api/describe-building', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ description, project_name: projectName }),
        });

        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || 'Failed to generate estimate');

        showResults(data);
    } catch (err) {
        alert(`Error: ${err.message}`);
    } finally {
        showLoading(false);
        document.querySelector('.loading-overlay p').textContent = 'Analyzing structural drawing...';
    }
}


// ===== Download Estimate =====
async function downloadEstimate(format) {
    const estimate = window._lastEstimate;
    if (!estimate) {
        alert('No estimate data available. Please run an estimation first.');
        return;
    }

    const endpoint = format === 'excel' ? '/api/download-excel' : '/api/download-csv';
    const btn = event.target.closest('.download-btn');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<span>Generating...</span>';
    btn.disabled = true;

    try {
        const response = await fetch(endpoint, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(estimate),
        });

        if (!response.ok) {
            const errData = await response.json();
            throw new Error(errData.detail || 'Download failed');
        }

        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;

        const disposition = response.headers.get('Content-Disposition');
        const filenameMatch = disposition && disposition.match(/filename="(.+)"/);
        a.download = filenameMatch ? filenameMatch[1] : `steel_estimate.${format === 'excel' ? 'xlsx' : 'csv'}`;

        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
    } catch (err) {
        alert(`Download failed: ${err.message}`);
    } finally {
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}
