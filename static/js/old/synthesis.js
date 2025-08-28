// --- static/js/synthesis.js ---
// Reverted to use CDN via ES Modules

import { Editor } from 'https://esm.sh/@tiptap/core@2.4.0';
import StarterKit from 'https://esm.sh/@tiptap/starter-kit@2.4.0';
import { CitationPill } from './CitationPill.js';

function debounce(func, wait) {
    let timeout;
    return function(...args) {
        const context = this;
        clearTimeout(timeout);
        timeout = setTimeout(() => func.apply(context, args), wait);
    };
}

function findAndCollectPillIds(node, ids) {
    if (node.type && node.type.name === 'citationPill' && node.attrs && node.attrs['data-doc-id'] && !node.attrs['data-doc-type']) {
        ids.add(node.attrs['data-doc-id']);
    }
    if (node.content) {
        node.content.forEach(child => findAndCollectPillIds(child, ids));
    }
}

function updatePillsWithTypes(node, typeMap) {
    if (node.type && node.type.name === 'citationPill' && node.attrs && node.attrs['data-doc-id']) {
        const docId = node.attrs['data-doc-id'];
        if (typeMap[docId]) {
            node.attrs['data-doc-type'] = typeMap[docId];
        }
    }
    if (node.content) {
        node.content.forEach(child => updatePillsWithTypes(child, typeMap));
    }
    return node;
}

// This async wrapper is necessary when using top-level await in modules
(async () => {
    // === SELECTORS ===
    const synthesisContainer = document.querySelector('.synthesis-container');
    const saveStatusElement = document.querySelector('#save-status');
    const editorElement = document.querySelector('#tiptap-editor');
    const bibliographyContent = document.querySelector('#bibliography-content');
    const referenceViewerFrame = document.querySelector('#reference-viewer');
    const searchInput = document.querySelector('#kb-search-input');
    const dynamicSearchResults = document.querySelector('#dynamic-search-results');
    const backToDocViewBtn = document.getElementById('back-to-doc-view-btn');
    const reportTitleDisplay = document.getElementById('report-title-display');
    const reportActionsBtn = document.getElementById('report-actions-btn');
    const reportActionsMenu = document.getElementById('report-actions-menu');
    const createReportBtn = document.getElementById('create-report-btn');
    const renameReportBtn = document.getElementById('rename-report-btn');
    const deleteReportBtn = document.getElementById('delete-report-btn');
    const exportOdtBtn = document.querySelector('#export-odt-btn');
    const reportListContainer = document.getElementById('report-list-container');
    const popupTemplate = document.querySelector('#cite-popup-template');
    const modal = document.querySelector('#citation-modal');
    const modalSourceInfo = document.querySelector('#modal-source-info');
    const modalQuotedText = document.querySelector('#modal-quoted-text');
    const modalPrefix = document.querySelector('#modal-prefix');
    const modalSuffix = document.querySelector('#modal-suffix');
    const modalSuppressAuthor = document.querySelector('#modal-suppress-author');
    const modalInsertQuoteBtn = document.querySelector('#modal-insert-quote-btn');
    const modalInsertCitationBtn = document.querySelector('#modal-insert-citation-btn');
    let activePopup = null;
    let citationPayload = null;

    // === CORE FUNCTIONS ===
    async function updateBibliography(editorInstance) {
        const content = editorInstance.getJSON();
        try {
            const response = await fetch(`/api/synthesis/${REPORT_ID}/bibliography`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
                body: JSON.stringify(content)
            });
            if (!response.ok) throw new Error('Network error');
            const data = await response.json();
            bibliographyContent.innerHTML = data.html;
        } catch (error) {
            console.error("Bibliography update failed:", error);
            bibliographyContent.innerHTML = '<p class="text-danger">Could not load bibliography.</p>';
        }
    }
    const debouncedUpdateBibliography = debounce(updateBibliography, 250);
    const debouncedSaveContent = debounce(async (editorInstance) => {
        updateSaveStatus('Saving...');
        try {
            const response = await fetch(`/api/synthesis/${REPORT_ID}/content`, {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify(editorInstance.getJSON()),
            });
            const result = await response.json();
            if (!result.success) throw new Error(result.message);
            updateSaveStatus('Saved.');
        } catch (error) { console.error('Save failed:', error); updateSaveStatus('Save failed.'); }
    }, 1500);
    function updateSaveStatus(status) { saveStatusElement.textContent = status; saveStatusElement.style.opacity = '1'; if (status === 'Saved.') setTimeout(() => { saveStatusElement.style.opacity = '0'; }, 2000); }

    async function loadAndPrepareContent() {
        let content;
        try {
            const response = await fetch(`/api/synthesis/${REPORT_ID}/content`);
            const data = await response.json();
            content = (data.success && data.content) ? data.content : {type: 'doc', content: [{type: 'paragraph'}]};
        } catch (error) {
            console.error('Failed to load content:', error);
            saveStatusElement.textContent = 'Error loading content.';
            return {type: 'doc', content: [{type: 'paragraph', content: [{type: 'text', text: 'Error loading content.'}]}]};
        }

        const idsToFetch = new Set();
        findAndCollectPillIds(content, idsToFetch);

        if (idsToFetch.size > 0) {
            console.log(`Found ${idsToFetch.size} legacy citation pills. Attempting to upgrade...`);
            try {
                const response = await fetch('/api/documents/types', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
                    body: JSON.stringify({ doc_ids: Array.from(idsToFetch) })
                });
                const typeMap = await response.json();
                content = updatePillsWithTypes(content, typeMap);
                console.log('Upgrade complete.');
            } catch (error) {
                console.error('Failed to upgrade legacy citations:', error);
            }
        }
        return content;
    }

    const initialContent = await loadAndPrepareContent();
    const editor = new Editor({
        element: editorElement,
        extensions: [ StarterKit, CitationPill ],
        content: initialContent,
        editorProps: { attributes: { class: 'ProseMirror' } },
        onUpdate: ({ editor }) => {
            debouncedSaveContent(editor);
            debouncedUpdateBibliography(editor);
        },
    });

    // === DOCUMENT SEARCH & VIEW LOGIC ===
    const performSearch = debounce(async () => {
        const query = searchInput.value.trim();
        if (query.length < 2) { dynamicSearchResults.style.display = 'none'; return; }
        try {
            const response = await fetch(`/api/synthesis/search/documents?q=${encodeURIComponent(query)}`);
            const results = await response.json();
            const resultsToShow = results.slice(0, 5);
            if (resultsToShow.length === 0) {
                dynamicSearchResults.innerHTML = '<div class="panel-body text-muted" style="padding: 1rem;">No documents found.</div>';
            } else {
                let listHtml = '<ul style="list-style: none; padding: 0; margin: 0;">';
                resultsToShow.forEach(doc => {
                    const lastSlash = doc.relative_path.lastIndexOf('/');
                    const filename = lastSlash === -1 ? doc.relative_path : doc.relative_path.substring(lastSlash + 1);
                    const directory = lastSlash === -1 ? './' : doc.relative_path.substring(0, lastSlash + 1);
                    
                    let timestamp = '';
                    if (doc.file_modified_at) {
                        timestamp = new Date(doc.file_modified_at).toISOString().slice(0, 16).replace('T', ' ');
                    }

                    listHtml += `
                        <li class="search-result-item">
                            <a href="#" data-doc-id="${doc.id}" data-file-type="${doc.file_type}" title="${doc.relative_path}" style="display: flex; flex-direction: column; padding: 0.5rem 0.75rem;">
                                <strong style="font-size: 1.1em;">${filename}</strong>
                                <small class="text-muted" style="font-family: monospace;">
                                    ${directory}
                                    <span style="margin-left: 1em; color: var(--text-muted)">${timestamp}</span>
                                </small>
                            </a>
                        </li>`;
                });
                listHtml += '</ul>';
                dynamicSearchResults.innerHTML = `<div class="panel-body">${listHtml}</div>`;
            }
            dynamicSearchResults.style.display = 'block';
        } catch (error) { console.error('Search failed:', error); dynamicSearchResults.innerHTML = '<div class="panel-body text-danger" style="padding: 1rem;">Search failed.</div>'; dynamicSearchResults.style.display = 'block'; }
    }, 300);
    searchInput.addEventListener('input', performSearch);
    
    // --- START OF FIX 1 ---
    function loadDocumentInViewer(docId, fileType, docPath, lastAudioPosition = 0) {
        let viewerUrl;
        const cacheBuster = `v=${new Date().getTime()}`;
    
        if (fileType === 'PDF') { viewerUrl = `/view_pdf/${docId}?${cacheBuster}`; } 
        else if (fileType === 'TXT') { viewerUrl = `/view_text/${docId}?${cacheBuster}`; } 
        else if (fileType === 'HTML') { viewerUrl = `/view_html/${docId}?${cacheBuster}`; } 
        else if (fileType === 'SRT') { 
            // Pass the last known position as a URL parameter for the viewer to use.
            viewerUrl = `/view_srt/${docId}?position=${lastAudioPosition}&${cacheBuster}`;
        } else { 
            viewerUrl = `/serve_doc/${docPath}?${cacheBuster}`; 
        }
    
        referenceViewerFrame.src = viewerUrl;
        backToDocViewBtn.href = `/document/${docId}`;
        backToDocViewBtn.style.display = 'inline-flex';
        sessionStorage.setItem('redleaf_lastViewedDoc', JSON.stringify({ id: docId, type: fileType, path: docPath, last_audio_position: lastAudioPosition }));
    }
    // --- END OF FIX 1 ---
    
    dynamicSearchResults.addEventListener('click', (e) => {
        e.preventDefault();
        const link = e.target.closest('a');
        if (!link) return;
        const docId = link.dataset.docId;
        const fileType = link.dataset.fileType;
        const docPath = link.title; 
        // When loading from search, we don't know the position, so it defaults to 0.
        loadDocumentInViewer(docId, fileType, docPath, 0);
        dynamicSearchResults.style.display = 'none';
        searchInput.value = '';
    });


    // === CITATION LOGIC ===
    window.addEventListener('message', (event) => { if (event.source === referenceViewerFrame.contentWindow && event.data.type === 'textSelected') { citationPayload = event.data.payload; showCitePopup(event.data.x, event.data.y); } });
    function showCitePopup(x, y) { removeCitePopup(); const popup = popupTemplate.firstElementChild.cloneNode(true); popup.style.left = `${x + 5}px`; popup.style.top = `${y}px`; popup.addEventListener('click', (e) => { if (e.target.dataset.action) showCitationModal(); }); document.body.appendChild(popup); activePopup = popup; setTimeout(() => document.addEventListener('click', handleOutsideClick, { capture: true, once: true }), 0); }
    function removeCitePopup() { if (activePopup) activePopup.remove(); activePopup = null; }
    function handleOutsideClick(event) { if (activePopup && !activePopup.contains(event.target)) removeCitePopup(); }
    
    function showCitationModal() {
        if (!citationPayload) return;
        modal.dataset.citationPayload = JSON.stringify(citationPayload);
        modalSourceInfo.innerHTML = `Doc ID: <code>${citationPayload.source_doc_id}</code>, Page: <code>${citationPayload.page_number}</code>`;
        modalQuotedText.value = citationPayload.selected_text || '';
        modalPrefix.value = '';
        modalSuffix.value = '';
        modalSuppressAuthor.checked = false;
        modal.classList.add('show');
        modalQuotedText.focus();
    }
    
    const closeButtons = modal.querySelectorAll('.modal-close-btn, #modal-cancel-btn');
    closeButtons.forEach(btn => btn.addEventListener('click', () => modal.classList.remove('show')));
    modal.addEventListener('click', (e) => { if (e.target === modal) modal.classList.remove('show'); });
    
    async function handleInsert(asQuote) {
        if (!modal.dataset.citationPayload) {
            alert("Error: Citation data is missing. Please try again.");
            return;
        }
        const payload = JSON.parse(modal.dataset.citationPayload);

        const dataToSend = {
            source_doc_id: payload.source_doc_id,
            page_number: payload.page_number,
            corrected_text: asQuote ? modalQuotedText.value.trim() : null,
            prefix: modalPrefix.value.trim(),
            suffix: modalSuffix.value.trim(),
            suppress_author: modalSuppressAuthor.checked
        };

        try {
            modalInsertQuoteBtn.disabled = true; 
            modalInsertCitationBtn.disabled = true; 
            modalInsertQuoteBtn.textContent = 'Inserting...';
            
            const response = await fetch(`/api/synthesis/${REPORT_ID}/citations`, { 
                method: 'POST', 
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, 
                body: JSON.stringify(dataToSend), 
            });
            const result = await response.json();
            
            if (!response.ok || !result.success) { 
                throw new Error(result.message || 'Failed to add citation.'); 
            }

            const chain = editor.chain().focus();

            if (asQuote && dataToSend.corrected_text) {
                chain.insertContent(`"${dataToSend.corrected_text}" `);
            }
            
            chain.insertCitation({
                'data-citation-uuid': result.citation_instance_uuid,
                'data-doc-id': result.data_doc_id,
                'data-doc-page': dataToSend.page_number,
                'data-doc-type': result.data_doc_type,
                'labelText': result.in_text_label
            }).insertContent(' ').run();
            
            modal.classList.remove('show');
            await updateBibliography(editor);
            document.getElementById('bibliography-pane').scrollIntoView({ behavior: 'smooth', block: 'nearest' });

        } catch (error) {
            alert(`Could not add citation: ${error.message}`);
        } finally {
            modalInsertQuoteBtn.disabled = false;
            modalInsertCitationBtn.disabled = false;
            modalInsertQuoteBtn.textContent = 'Insert as Quote';
            delete modal.dataset.citationPayload;
        }
    }

    modalInsertQuoteBtn.addEventListener('click', () => handleInsert(true));
    modalInsertCitationBtn.addEventListener('click', () => handleInsert(false));
    
    // --- START OF FIX 2 ---
    editorElement.addEventListener('click', (e) => {
        const pill = e.target.closest('.citation-pill');
        if (pill) {
            const docId = pill.dataset.docId;
            const page = pill.dataset.docPage;
            const fileType = pill.dataset.docType;
            if (!fileType) { alert('This citation is from an older version and is missing file type data. Please delete and re-create it to restore its functionality.'); return; }
            
            const currentSrc = new URL(referenceViewerFrame.src, window.location.href);
            let viewerPath;

            if (fileType === 'SRT') {
                viewerPath = `/view_srt/${docId}`;
                if (currentSrc.pathname.includes(viewerPath)) {
                    referenceViewerFrame.contentWindow.postMessage({ type: 'scrollToCue', cue: page }, '*');
                } else {
                    // Corrected API endpoint from 'audio_status' to 'media_status'
                    fetch(`/api/document/${docId}/media_status`)
                        .then(res => res.json())
                        .then(data => {
                            // Pass the fetched position to the viewer loading function
                            loadDocumentInViewer(docId, fileType, '', data.position || 0);
                        })
                        .catch(err => {
                             console.error("Could not fetch media status, loading at start.", err);
                             loadDocumentInViewer(docId, fileType, '', 0);
                        });
                }
            } else {
                if (fileType === 'PDF') { viewerPath = `/view_pdf/${docId}`; } 
                else if (fileType === 'TXT') { viewerPath = `/view_text/${docId}`; } 
                else if (fileType === 'HTML') { viewerPath = `/view_html/${docId}`; }

                if (viewerPath && !currentSrc.pathname.includes(viewerPath)) {
                    loadDocumentInViewer(docId, fileType, '');
                } else if (viewerPath) {
                    referenceViewerFrame.contentWindow.postMessage({ type: 'scrollToPage', page: page }, '*');
                }
            }
        }
    });
    // --- END OF FIX 2 ---

    // === REPORT MANAGEMENT & INITIALIZATION ===
    async function loadAndRenderReportList() {
        try {
            const response = await fetch('/api/synthesis/reports');
            const reports = await response.json();
            reportListContainer.innerHTML = '<strong class="dropdown-item" style="pointer-events: none; padding-bottom: 0.25rem;">Switch to Report:</strong>';
            if (reports.length <= 1) { deleteReportBtn.style.display = 'none'; } else { deleteReportBtn.style.display = 'block'; }
            if (reports.length > 1) { reports.forEach(report => { if (report.id !== REPORT_ID) { const link = document.createElement('a'); link.href = `/synthesis/report/${report.id}`; link.className = 'dropdown-item'; link.textContent = report.title; reportListContainer.appendChild(link); } }); } 
            else { reportListContainer.innerHTML += '<span class="dropdown-item text-muted" style="font-style: italic;">No other reports</span>'; }
        } catch (error) { console.error('Failed to load reports:', error); reportListContainer.innerHTML = '<span class="dropdown-item text-danger">Error loading reports.</span>'; }
    }
    reportActionsBtn.addEventListener('click', (e) => { e.stopPropagation(); reportActionsMenu.classList.toggle('show'); });
    document.addEventListener('click', (e) => { if (!reportActionsMenu.contains(e.target) && reportActionsMenu.classList.contains('show')) { reportActionsMenu.classList.remove('show'); } });
    createReportBtn.addEventListener('click', async () => { const title = prompt("Enter a name for the new report (or leave blank for an auto-generated name):"); if (title === null) return; const response = await fetch('/api/synthesis/reports', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ title: title.trim() }) }); const result = await response.json(); if (result.success) { window.location.href = `/synthesis/report/${result.report.id}`; } else { alert(`Error creating report: ${result.message}`); } });
    renameReportBtn.addEventListener('click', async (e) => { e.preventDefault(); const currentTitle = reportTitleDisplay.textContent; const newTitle = prompt("Enter a new name for this report:", currentTitle); if (!newTitle || newTitle.trim() === '' || newTitle.trim() === currentTitle) return; const response = await fetch(`/api/synthesis/report/${REPORT_ID}`, { method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ title: newTitle.trim() }) }); const result = await response.json(); if (result.success) { reportTitleDisplay.textContent = newTitle.trim(); document.title = `Synthesis: ${newTitle.trim()}`; reportActionsMenu.classList.remove('show'); } else { alert(`Error renaming report: ${result.message}`); } });
    deleteReportBtn.addEventListener('click', async (e) => { e.preventDefault(); if (!confirm(`Are you sure you want to permanently delete the report "${reportTitleDisplay.textContent}"? This cannot be undone.`)) { return; } const response = await fetch(`/api/synthesis/report/${REPORT_ID}`, { method: 'DELETE', headers: { 'X-CSRFToken': CSRF_TOKEN } }); const result = await response.json(); if (result.success) { window.location.href = '/synthesis/'; } else { alert(`Error deleting report: ${result.message}`); } });
    
    if (exportOdtBtn) {
        exportOdtBtn.addEventListener('click', async () => {
            const originalText = exportOdtBtn.textContent;
            exportOdtBtn.textContent = 'Exporting...'; exportOdtBtn.disabled = true;
            try {
                const htmlContent = editor.getHTML();
                const response = await fetch(`/api/synthesis/${REPORT_ID}/export/odt`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ html_content: htmlContent }) });
                if (!response.ok) { const errorData = await response.json(); throw new Error(errorData.message || 'Export failed on the server.'); }
                const blob = await response.blob();
                const url = window.URL.createObjectURL(blob); const a = document.createElement('a'); a.style.display = 'none'; a.href = url;
                const disposition = response.headers.get('Content-Disposition'); let filename = 'synthesis-report.odt';
                if (disposition && disposition.indexOf('attachment') !== -1) { const filenameRegex = /filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/; const matches = filenameRegex.exec(disposition); if (matches != null && matches[1]) { filename = matches[1].replace(/['"]/g, ''); } }
                a.setAttribute('download', filename); document.body.appendChild(a); a.click(); window.URL.revokeObjectURL(url);
            } catch (error) { console.error('Export failed:', error); alert(`Error: Could not export document. ${error.message}`);
            } finally { exportOdtBtn.textContent = originalText; exportOdtBtn.disabled = false; }
        });
    }

    // Read the global variable set in the template
    const docToLoadData = window.DOC_TO_LOAD;
    if (docToLoadData) {
        try {
            // It's already a JS object, no parsing needed
            loadDocumentInViewer(docToLoadData.id, docToLoadData.file_type, docToLoadData.relative_path, docToLoadData.last_audio_position);
        } catch (e) {
            console.error("Failed to process doc_to_load data:", e);
        }
    } else {
        // Fallback to session storage for persistence
        const lastViewedDocJSON = sessionStorage.getItem('redleaf_lastViewedDoc');
        if (lastViewedDocJSON) { 
            try { 
                const lastViewedDoc = JSON.parse(lastViewedDocJSON); 
                loadDocumentInViewer(lastViewedDoc.id, lastViewedDoc.type, lastViewedDoc.path || '', lastViewedDoc.last_audio_position); 
            } catch (e) { 
                console.error("Could not parse last viewed doc from session storage", e); 
                sessionStorage.removeItem('redleaf_lastViewedDoc'); 
            } 
        }
    }
    
    updateBibliography(editor);
    loadAndRenderReportList();
    
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('first_visit') === 'true') {
        setTimeout(async () => {
            const newTitle = prompt("Welcome! Please name your first synthesis report:", "Main Project");
            if (newTitle && newTitle.trim() !== '') {
                const response = await fetch(`/api/synthesis/report/${REPORT_ID}`, {
                    method: 'PUT',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
                    body: JSON.stringify({ title: newTitle.trim() })
                });
                const result = await response.json();
                if (result.success) {
                    reportTitleDisplay.textContent = newTitle.trim();
                    document.title = `Synthesis: ${newTitle.trim()}`;
                } else {
                    alert(`Error renaming report: ${result.message}`);
                }
            }
            history.replaceState(null, '', window.location.pathname);
        }, 100);
    }

    // --- Vertical Resizer Logic ---
    const resizer = document.getElementById('resizer-vertical');
    const mainArea = document.querySelector('.synthesis-main-area');
    const sidebar = document.querySelector('.synthesis-sidebar');
    const container = document.querySelector('.synthesis-container');

    const savedLeftWidth = localStorage.getItem('synthesis-left-width');
    if (savedLeftWidth) {
        container.style.gridTemplateColumns = `${savedLeftWidth} auto 1fr`;
    }

    const mouseMoveHandler = (e) => {
        const dx = e.clientX - x;
        const newLeftWidth = (leftWidth + dx);
        
        const sidebarMinWidth = 400; 
        const mainAreaMinWidth = 300;
        const totalWidth = container.offsetWidth;

        if (newLeftWidth < mainAreaMinWidth || totalWidth - newLeftWidth < sidebarMinWidth) {
            return;
        }

        container.style.gridTemplateColumns = `${newLeftWidth}px auto 1fr`;
    };

    const mouseUpHandler = () => {
        document.body.classList.remove('is-resizing');
        document.removeEventListener('mousemove', mouseMoveHandler);
        document.removeEventListener('mouseup', mouseUpHandler);

        const newLeftWidth = mainArea.getBoundingClientRect().width;
        localStorage.setItem('synthesis-left-width', `${newLeftWidth}px`);
    };

    let x = 0;
    let leftWidth = 0;

    resizer.addEventListener('mousedown', (e) => {
        x = e.clientX;
        leftWidth = mainArea.getBoundingClientRect().width;
        document.body.classList.add('is-resizing');
        document.addEventListener('mousemove', mouseMoveHandler);
        document.addEventListener('mouseup', mouseUpHandler);
    });

})();