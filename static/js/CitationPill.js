// --- File: static/js/CitationPill.js ---

import { Node, mergeAttributes } from 'https://esm.sh/@tiptap/core@2.4.0';

export const CitationPill = Node.create({
    name: 'citationPill',
    group: 'inline',
    inline: true,
    atom: true,
    selectable: true,

    addAttributes() {
        return {
            'data-citation-uuid': {
                default: null,
                parseHTML: element => element.getAttribute('data-citation-uuid'),
            },
            'data-doc-id': {
                default: null,
                parseHTML: element => element.getAttribute('data-doc-id'),
            },
            'data-doc-page': {
                default: null,
                parseHTML: element => element.getAttribute('data-doc-page'),
            },
            // === FIX: The default is now null, not 'PDF' ===
            'data-doc-type': {
                default: null, // This prevents old citations from defaulting to PDF
                parseHTML: element => element.getAttribute('data-doc-type'),
            },
            // ===============================================
            labelText: {
                default: 'citation',
                parseHTML: element => element.textContent,
            },
            class: {
                default: 'citation-pill',
            },
        };
    },

    parseHTML() {
        return [{ 
            tag: 'span.citation-pill[data-citation-uuid]',
        }];
    },

    renderHTML({ node, HTMLAttributes }) {
        return ['span', mergeAttributes(HTMLAttributes), node.attrs.labelText];
    },

    addCommands() {
        return {
            insertCitation: (attributes) => ({ commands }) => {
                return commands.insertContent({
                    type: this.name,
                    attrs: attributes,
                });
            },
        };
    },
});