// --- File: static/js/document_view.js ---

document.addEventListener('DOMContentLoaded', () => {
    // === GLOBAL ELEMENT SELECTORS ===
    const workbenchContainer = document.querySelector('.workbench-container');
    const docId = workbenchContainer.dataset.docId;

    // === FIX START: ADDED SELECTORS FOR HTML VIEW TOGGLE ===
    const toggleHtmlViewBtn = document.getElementById('toggle-html-view-btn');
    const docViewerIframe = document.getElementById('doc-viewer');
    // === FIX END ===

    // --- Tab Switching Logic (Unchanged) ---
    const sidebar = document.querySelector('.workbench-sidebar');
    if (sidebar) {
        const tabLinks = sidebar.querySelectorAll('.tab-link');
        const tabContents = sidebar.querySelectorAll('.tab-content');
        tabLinks.forEach(link => {
            link.addEventListener('click', (event) => {
                event.preventDefault();
                const tabId = link.dataset.tab;
                tabLinks.forEach(l => l.classList.remove('active'));
                link.classList.add('active');
                tabContents.forEach(content => {
                    content.classList.toggle('active', content.id === `tab-${tabId}`);
                });
            });
        });
    }

    // === FIX START: ADDED EVENT LISTENER FOR THE TOGGLE BUTTON ===
    if (toggleHtmlViewBtn && docViewerIframe) {
        const docPath = workbenchContainer.dataset.docPath;
        const rawViewUrl = `/serve_doc/${docPath}`;
        const strippedViewUrl = `/view_html/${docId}`;

        toggleHtmlViewBtn.addEventListener('click', () => {
            const currentSrc = docViewerIframe.getAttribute('src');
            // Check if the current view is the raw, original HTML file by checking for '/serve_doc/'
            if (currentSrc.includes('/serve_doc/')) {
                // If it is, switch to the stripped-down, citation-enabled view
                docViewerIframe.setAttribute('src', strippedViewUrl);
                toggleHtmlViewBtn.textContent = 'Show Original HTML';
            } else {
                // Otherwise, switch back to the raw, original HTML file
                docViewerIframe.setAttribute('src', rawViewUrl);
                toggleHtmlViewBtn.textContent = 'Show Stripped Text';
            }
        });
    }
    // === FIX END ===

    // --- CURATION TAB LOGIC (This is your existing, complete code) ---
    const curationTab = document.getElementById('tab-curation');
    if (curationTab) {
        // --- Color Palette ---
        const colorPalette = document.getElementById('color-palette');
        const initialColor = workbenchContainer.dataset.docColor;
        if(initialColor) {
            const selected = colorPalette.querySelector(`[data-color="${initialColor}"]`);
            if(selected) selected.classList.add('selected');
        }
        colorPalette.addEventListener('click', (e) => {
            const colorSpan = e.target.closest('.palette-color');
            if (!colorSpan) return;
            const color = colorSpan.dataset.color;
            fetch(`/api/document/${docId}/color`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
                body: JSON.stringify({ color: color })
            }).then(() => {
                colorPalette.querySelectorAll('.palette-color').forEach(el => el.classList.remove('selected'));
                colorSpan.classList.add('selected');
            });
        });

        // --- Note Saving ---
        const noteTextarea = document.getElementById('note-content');
        const saveNoteBtn = document.getElementById('save-curation-btn');
        saveNoteBtn.addEventListener('click', () => {
            fetch(`/api/document/${docId}/curation`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
                body: JSON.stringify({ note: noteTextarea.value })
            }).then(() => {
                saveNoteBtn.textContent = 'Saved!';
                setTimeout(() => { saveNoteBtn.textContent = 'Save Note'; }, 2000);
            });
        });
        
        // --- Comments ---
        const commentList = document.getElementById('comment-list');
        const newCommentText = document.getElementById('new-comment-text');
        const postCommentBtn = document.getElementById('post-comment-btn');
        function renderComments(comments, currentUser) {
            commentList.innerHTML = '';
            if (comments.length === 0) {
                commentList.innerHTML = '<p class="text-muted">No comments yet.</p>';
                return;
            }
            comments.forEach(comment => {
                const canDelete = currentUser.role === 'admin' || currentUser.id === comment.user_id;
                const deleteBtnHtml = canDelete ? `<button class="button-delete" data-comment-id="${comment.id}" title="Delete comment">×</button>` : '';
                const commentDate = new Date(comment.created_at).toLocaleString();
                const commentEl = document.createElement('div');
                commentEl.className = 'comment-item';
                commentEl.innerHTML = `<div class="comment-header"><strong>${comment.username}</strong>${deleteBtnHtml}</div><p class="comment-body">${comment.comment_text}</p><small class="text-muted">${commentDate}</small>`;
                commentList.appendChild(commentEl);
            });
        }
        postCommentBtn.addEventListener('click', async () => {
            const text = newCommentText.value.trim();
            if (!text) return;
            const response = await fetch(`/api/document/${docId}/comments`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ comment_text: text }) });
            if (response.ok) { newCommentText.value = ''; loadCurationData(); }
        });
        commentList.addEventListener('click', async (e) => {
            if (e.target.classList.contains('button-delete')) {
                const commentId = e.target.dataset.commentId;
                if (!confirm('Are you sure you want to delete this comment?')) return;
                const response = await fetch(`/api/comments/${commentId}`, { method: 'DELETE', headers: { 'X-CSRFToken': CSRF_TOKEN } });
                if (response.ok) loadCurationData();
            }
        });

        // --- Tags ---
        const tagContainer = document.getElementById('tag-container');
        const tagInput = document.getElementById('tag-input');
        function renderTags(tags) {
            tagContainer.innerHTML = '';
            tags.forEach(tag => {
                const tagEl = document.createElement('span');
                tagEl.className = 'tag-item';
                tagEl.textContent = tag;
                const removeBtn = document.createElement('button');
                removeBtn.className = 'tag-remove-btn';
                removeBtn.innerHTML = '×';
                removeBtn.dataset.tag = tag;
                tagEl.appendChild(removeBtn);
                tagContainer.appendChild(tagEl);
            });
        }
        async function saveTags(tags) {
            await fetch(`/api/document/${docId}/tags`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ tags: tags }) });
            loadCurationData();
        }
        tagInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') {
                e.preventDefault();
                const newTag = tagInput.value.trim().toLowerCase();
                if (!newTag) return;
                const currentTags = Array.from(tagContainer.querySelectorAll('.tag-item')).map(el => el.textContent.slice(0, -1));
                if (!currentTags.includes(newTag)) { saveTags([...currentTags, newTag]); tagInput.value = ''; }
            }
        });
        tagContainer.addEventListener('click', (e) => {
            if (e.target.classList.contains('tag-remove-btn')) {
                const tagToRemove = e.target.dataset.tag;
                let currentTags = Array.from(tagContainer.querySelectorAll('.tag-item')).map(el => el.textContent.slice(0, -1));
                currentTags = currentTags.filter(t => t !== tagToRemove);
                saveTags(currentTags);
            }
        });

        // --- Catalogs ---
        const catalogListContainer = document.getElementById('catalog-list-container');
        const favoriteToggle = document.getElementById('is-favorite-toggle');
        let favoritesCatalogId = null;
        function renderCatalogs(allCatalogs, memberOf, isFavorite) {
            favoritesCatalogId = allCatalogs.find(c => c.name === '⭐ Favorites')?.id;
            favoriteToggle.checked = isFavorite;
            catalogListContainer.innerHTML = '';
            allCatalogs.filter(c => c.id !== favoritesCatalogId).forEach(catalog => {
                const isMember = memberOf.includes(catalog.id);
                const catalogEl = document.createElement('div');
                catalogEl.className = 'form-check';
                catalogEl.innerHTML = `<input type="checkbox" class="form-check-input catalog-checkbox" id="cat-${catalog.id}" data-cat-id="${catalog.id}" ${isMember ? 'checked' : ''}><label class="form-check-label" for="cat-${catalog.id}">${catalog.name}</label>`;
                catalogListContainer.appendChild(catalogEl);
            });
        }
        async function updateCatalogMembership() {
            const memberOf = Array.from(catalogListContainer.querySelectorAll('.catalog-checkbox:checked')).map(cb => parseInt(cb.dataset.catId));
            if (favoriteToggle.checked && favoritesCatalogId && !memberOf.includes(favoritesCatalogId)) {
                memberOf.push(favoritesCatalogId);
            }
            await fetch(`/api/document/${docId}/catalogs`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ catalog_ids: memberOf }) });
        }
        catalogListContainer.addEventListener('change', updateCatalogMembership);
        favoriteToggle.addEventListener('change', updateCatalogMembership);
        
        // --- Data Loading & Main Function ---
        async function loadCurationData() {
            const response = await fetch(`/api/document/${docId}/curation`);
            const data = await response.json();
            noteTextarea.value = data.note;
            renderComments(data.comments, data.current_user);
            renderCatalogs(data.all_catalogs, data.member_of_catalogs, data.is_favorite);
            const tagResponse = await fetch(`/api/document/${docId}/tags`);
            const tagData = await tagResponse.json();
            renderTags(tagData.tags);
        }
        loadCurationData(); // Initial load

        const createCatalogBtn = document.getElementById('create-catalog-btn');
        const newCatalogNameInput = document.getElementById('new-catalog-name');

        createCatalogBtn.addEventListener('click', async () => {
            const catalogName = newCatalogNameInput.value.trim();
            if (!catalogName) {
                alert('Please enter a name for the new catalog.');
                return;
            }
            try {
                const response = await fetch('/api/catalogs', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN },
                    body: JSON.stringify({ name: catalogName })
                });

                const result = await response.json();
                if (!response.ok) {
                    throw new Error(result.message || 'Failed to create catalog.');
                }
                newCatalogNameInput.value = '';
                await loadCurationData();
                setTimeout(async () => {
                    const newCatalogCheckbox = catalogListContainer.querySelector(`.catalog-checkbox[id="cat-${result.catalog.id}"]`);
                    if (newCatalogCheckbox) {
                        newCatalogCheckbox.checked = true;
                        await updateCatalogMembership();
                    }
                }, 100);
            } catch (error) {
                alert(`Error: ${error.message}`);
            }
        });
    }

    // --- Metadata Panel Logic ---
    const metadataTab = document.getElementById('tab-metadata');
    if (metadataTab) {
        const typeSelect = document.getElementById('csl-type');
        const yearSelect = document.getElementById('csl-date-year');
        const monthSelect = document.getElementById('csl-date-month');
        const daySelect = document.getElementById('csl-date-day');
        const saveBtn = document.getElementById('save-metadata-btn');
        const saveStatus = document.getElementById('metadata-save-status');
        
        function populateYearSelect() { const currentYear = new Date().getFullYear(); yearSelect.innerHTML = '<option value="">Year</option>'; for (let y = currentYear + 1; y >= 1600; y--) yearSelect.add(new Option(y, y)); }
        function populateMonthSelect() { const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]; monthSelect.innerHTML = '<option value="">Month</option>'; months.forEach((month, i) => monthSelect.add(new Option(month, i + 1))); }
        function populateDaySelect() { daySelect.innerHTML = '<option value="">Day</option>'; for (let d = 1; d <= 31; d++) daySelect.add(new Option(d, d)); }
        
        function populateDocTypeSelect() {
            const types = { 
                'report': 'Report', 
                'article-journal': 'Journal Article', 
                'book': 'Book', 
                'webpage': 'Webpage',
                'broadcast': 'TV/Radio Broadcast',
                'interview': 'Interview/Podcast',
                'entry-encyclopedia': 'Encyclopedia Entry', 
                'chapter': 'Book Chapter', 
                'manuscript': 'Manuscript', 
                'bill': 'Bill / Legislation' 
            };
            typeSelect.innerHTML = ''; 
            for (const [value, text] of Object.entries(types)) typeSelect.add(new Option(text, value)); 
        }

        function updateFormFields() {
            const selectedType = typeSelect.value;
            document.querySelectorAll('.csl-field-specific').forEach(field => {
                const visibleFor = field.dataset.cslType.split(' ');
                field.style.display = visibleFor.includes(selectedType) ? 'block' : 'none';
            });
            
            const containerTitleLabel = document.querySelector('label[for="csl-container-title"]');
            const publisherLabel = document.querySelector('label[for="csl-publisher"]');

            if (selectedType === 'article-journal') {
                containerTitleLabel.textContent = 'Journal Title';
            } else if (selectedType === 'broadcast') {
                containerTitleLabel.textContent = 'Program / Series Title';
                publisherLabel.textContent = 'Network / Station';
            } else if (selectedType === 'interview') {
                containerTitleLabel.textContent = 'Podcast / Publication Series';
            } else { // Default labels
                publisherLabel.textContent = 'Publisher / Agency';
            }
        }

        function populateForm(cslData) { 
            const data = cslData || {}; 
            typeSelect.value = data.type || 'report'; 
            const docTitleElement = document.querySelector('.page-heading h1'); 
            document.getElementById('csl-title').value = data.title || (docTitleElement ? docTitleElement.textContent : ''); 
            document.getElementById('csl-author').value = (data.author || []).map(a => `${a.family || ''}, ${a.given || ''}`.trim().replace(/^,|,$/g, '')).join('; '); 
            if (data.issued && data.issued['date-parts'] && data.issued['date-parts'][0]) { 
                const [year, month, day] = data.issued['date-parts'][0]; 
                yearSelect.value = year || ''; 
                monthSelect.value = month || ''; 
                daySelect.value = day || ''; 
            } else { 
                yearSelect.value = ''; monthSelect.value = ''; daySelect.value = ''; 
            } 
            document.getElementById('csl-publisher').value = data.publisher || ''; 
            document.getElementById('csl-container-title').value = data['container-title'] || ''; 
            document.getElementById('csl-url').value = data.URL || ''; 
            updateFormFields(); 
        }
        
        function buildCslFromForm() {
            const csl = { id: `doc-${DOC_ID}`, type: typeSelect.value };
            const title = document.getElementById('csl-title').value.trim();
            if (title) csl.title = title;
            const authorString = document.getElementById('csl-author').value.trim();
            if (authorString) { csl.author = authorString.split(';').map(name => { const parts = name.trim().split(','); const authorObj = {}; if(parts[0] && parts[0].trim()) authorObj.family = parts[0].trim(); if(parts[1] && parts[1].trim()) authorObj.given = parts[1].trim(); return authorObj; }).filter(a => a.family || a.given); }
            const dateParts = [yearSelect.value, monthSelect.value, daySelect.value].filter(Boolean).map(p => parseInt(p, 10));
            if (dateParts.length > 0) csl.issued = { 'date-parts': [dateParts] };
            
            const publisherInput = document.getElementById('csl-publisher');
            const containerTitleInput = document.getElementById('csl-container-title');
            const urlInput = document.getElementById('csl-url');

            const publisher = publisherInput.value.trim();
            if (['report', 'broadcast'].includes(csl.type) && publisher) {
                csl.publisher = publisher;
            }

            const containerTitle = containerTitleInput.value.trim();
            if (['article-journal', 'broadcast', 'interview'].includes(csl.type) && containerTitle) {
                csl['container-title'] = containerTitle;
            }

            const url = urlInput.value.trim();
            if (['webpage', 'broadcast', 'interview'].includes(csl.type) && url) {
                csl.URL = url;
            }
            return csl;
        }

        async function loadMetadata() { 
            try { 
                const response = await fetch(`/api/document/${DOC_ID}/metadata`); 
                const data = await response.json(); 
                populateForm(data.csl_json ? JSON.parse(data.csl_json) : null); 
            } catch (error) { 
                saveStatus.textContent = "Error loading data."; 
                saveStatus.style.opacity = '1'; 
            } 
        }
        
        saveBtn.addEventListener('click', async () => { 
            const newCslData = buildCslFromForm(); 
            const cslJsonString = JSON.stringify(newCslData, null, 2); 
            saveBtn.disabled = true; 
            saveStatus.textContent = "Saving..."; 
            saveStatus.style.opacity = '1'; 
            try { 
                const response = await fetch(`/api/document/${DOC_ID}/metadata`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF_TOKEN }, body: JSON.stringify({ csl_json: cslJsonString }) }); 
                const result = await response.json(); 
                if (!response.ok) throw new Error(result.message); 
                saveStatus.textContent = "Saved successfully."; 
                setTimeout(() => { saveStatus.style.opacity = '0'; }, 2000); 
            } catch (error) { 
                saveStatus.textContent = `Error: ${error.message}`; 
            } finally { 
                saveBtn.disabled = false; 
            } 
        });
        
        typeSelect.addEventListener('change', updateFormFields);
        populateDocTypeSelect(); 
        populateYearSelect(); 
        populateMonthSelect(); 
        populateDaySelect(); 
        loadMetadata();
    }
});