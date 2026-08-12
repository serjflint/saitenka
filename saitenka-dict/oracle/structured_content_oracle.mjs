#!/usr/bin/env node

import process from 'node:process';
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import path from 'node:path';

const request = JSON.parse(await readStdin());
const root = path.resolve(request.checkout);
const fromYomitan = createRequire(path.join(root, 'package.json'));
const {JSDOM} = fromYomitan('jsdom');
const generatorUrl = pathToFileURL(
    path.join(root, 'ext/js/display/structured-content-generator.js'),
).href;
const {StructuredContentGenerator} = await import(generatorUrl);

const {window} = new JSDOM('<!doctype html><body></body>', {url: 'http://localhost/'});
globalThis.location = window.location;
const contentManager = {prepareLink() {}};
const generator = new StructuredContentGenerator(contentManager, window.document, window);
const rootElement = window.document.createElement('div');
for (const definition of request.definitions) {
    const definitionElement = window.document.createElement('div');
    definitionElement.dataset.oracleDefinition = 'true';
    rootElement.appendChild(definitionElement);
    for (const item of definition) {
        if (typeof item === 'string') {
            const element = window.document.createElement('div');
            element.textContent = item;
            definitionElement.appendChild(element);
        } else if (item?.type === 'structured-content') {
            definitionElement.appendChild(
                generator.createStructuredContent(item.content, request.dictionary),
            );
        } else if (item?.type === 'text') {
            const element = window.document.createElement('div');
            element.textContent = item.text ?? '';
            definitionElement.appendChild(element);
        } else if (item?.type === 'image') {
            definitionElement.appendChild(generator.createDefinitionImage(item, request.dictionary));
        }
    }
}

process.stdout.write(`${JSON.stringify({html: rootElement.innerHTML, blocks: blocks(rootElement)})}\n`);

function blocks(rootNode) {
    const output = [];
    for (const child of rootNode.children) visit(child, -1, false, undefined, output);
    return output;
}

function visit(node, listDepth, insideListItem, inheritedMarker, output) {
    if (node.nodeType !== window.Node.ELEMENT_NODE) return inheritedMarker;
    const tag = node.tagName.toLowerCase();
    if (['ul', 'ol'].includes(tag)) {
        const firstItem = [...node.children].find((child) => child.tagName === 'LI');
        const firstMarkerless = firstItem !== undefined && listMarker(firstItem) === '';
        const transfersParent = insideListItem && inheritedMarker !== undefined && firstMarkerless;
        if (!firstMarkerless || (insideListItem && !transfersParent)) listDepth += 1;
    }
    const text = directText(node);
    let pendingMarker = inheritedMarker;
    if (tag === 'li') {
        const marker = listMarker(node);
        if (pendingMarker !== undefined && marker !== '') {
            if (pendingMarker !== '') {
                output.push({marker: pendingMarker, depth: Math.max(0, listDepth - 1), text: ''});
            }
            pendingMarker = marker;
        }
        if (text) {
            emitRows(
                output,
                pendingMarker === undefined || marker !== '' ? marker : pendingMarker,
                Math.max(0, listDepth),
                text,
            );
            pendingMarker = undefined;
        } else if (pendingMarker === undefined) {
            pendingMarker = marker;
        }
    } else if (['div', 'p', 'details', 'summary'].includes(tag) && text) {
        emitRows(
            output,
            pendingMarker === undefined ? null : pendingMarker,
            Math.max(0, listDepth + (insideListItem && pendingMarker === undefined ? 1 : 0)),
            text,
        );
        pendingMarker = undefined;
    } else if (tag === 'table') {
        const depth = Math.max(
            0,
            listDepth + (insideListItem && pendingMarker === undefined ? 1 : 0),
        );
        const rows = [...node.rows];
        for (const [index, row] of rows.entries()) {
            const rowText = [...row.cells]
            .map((cell) => inlineText(cell))
            .filter(Boolean)
            .join(' │ ');
            emitRows(
                output,
                index === 0 && pendingMarker !== undefined ? pendingMarker : null,
                depth,
                rowText,
            );
        }
        return undefined;
    }
    for (const child of node.children) {
        pendingMarker = visit(
            child,
            listDepth,
            insideListItem || tag === 'li',
            pendingMarker,
            output,
        );
    }
    return pendingMarker;
}

function listMarker(node) {
    const parent = node.parentElement;
    let value = node.style.listStyleType || parent?.style.listStyleType || '';
    if (!value && parent?.dataset.scContent === 'glossary') value = 'none';
    if (value === 'none') return '';
    if (
        (value.startsWith('"') && value.endsWith('"')) ||
        (value.startsWith("'") && value.endsWith("'"))
    ) return value.slice(1, -1);
    if (value && !['disc', 'decimal'].includes(value)) {
        throw new Error(`unsupported list-style-type: ${value}`);
    }
    if (value === 'decimal' || (!value && parent?.tagName === 'OL')) {
        const index = [...parent.children].filter((child) => child.tagName === 'LI').indexOf(node);
        return `${index + 1}.`;
    }
    return '・';
}

function emitRows(output, marker, depth, text) {
    for (const [index, row] of text.split('\n').entries()) {
        if (!row) continue;
        output.push({marker: index === 0 ? marker : null, depth, text: row});
    }
}

function directText(node) {
    if (node.tagName === 'IMG') return node.alt || '[image]';
    let text = '';
    for (const child of node.childNodes) {
        if (child.nodeType === window.Node.TEXT_NODE) {
            text += child.textContent;
        } else if (
            child.nodeType === window.Node.ELEMENT_NODE &&
            !['UL', 'OL', 'DIV', 'P', 'DETAILS', 'SUMMARY', 'TABLE'].includes(child.tagName)
        ) {
            text += child.classList.contains('structured-content')
                ? directText(child)
                : inlineText(child);
        }
    }
    return normalize(text);
}

function inlineText(node) {
    if (node.nodeType === window.Node.TEXT_NODE) return node.textContent;
    if (node.nodeType !== window.Node.ELEMENT_NODE || node.tagName === 'RT') return '';
    if (node.tagName === 'IMG' || node.classList.contains('gloss-image-link')) return '[image]';
    if (node.tagName === 'BR') return '\n';
    return normalize([...node.childNodes].map(inlineText).join(''));
}

function normalize(text) {
    return text
        .split('\n')
        .map((line) => line.replace(/\s+/gu, ' ').trim())
        .join('\n');
}

async function readStdin() {
    let input = '';
    for await (const chunk of process.stdin) input += chunk;
    return input;
}
