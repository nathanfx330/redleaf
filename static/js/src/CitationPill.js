// --- File: static/js/CitationPill.js ---
// Reverted to use CDN via ES Modules

import { Node, mergeAttributes } from '@tiptap/core';

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
                renderHTML: attributes => ({ 'data-citation-uuid': attributes['data-citation-uuid'] }),
            },
            'data-doc-id': {
                default: null,
                parseHTML: element => element.getAttribute('data-doc-id'),
                renderHTML: attributes => ({ 'data-doc-id': attributes['data-doc-id'] }),
            },
            'data-doc-page': {
                default: null,
                parseHTML: element => element.getAttribute('data-doc-page'),
                renderHTML: attributes => ({ 'data-doc-page': attributes['data-doc-page'] }),
            },
            'data-doc-type': {
                default: null,
                parseHTML: element => element.getAttribute('data-doc-type'),
                renderHTML: attributes => ({ 'data-doc-type': attributes['data-doc-type'] }),
            },
            // This is the attribute for the visible text of the pill
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
        // Use the 'labelText' attribute to render the pill's text content
        return ['span', mergeAttributes(this.options.HTMLAttributes, HTMLAttributes), node.attrs.labelText];
    },

    addCommands() {
        return {
            // --- THIS IS THE FIX ---
            // The command now directly accepts all the attributes needed to create the node.
            insertCitation: (attributes) => ({ commands }) => {
                return commands.insertContent({
                    type: this.name,
                    attrs: attributes,
                });
            },
            // --- END FIX ---
        };
    },
});