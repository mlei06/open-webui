#!/usr/bin/env python3
"""Convert a .pptx deck to searchable Markdown (slide text, tables, speaker notes, link targets).

Used once to produce the committed knowledge/sops/*.md from the SOP decks; the large binary
decks themselves are never committed. Images are dropped except for their alt text. Reads the
deck as a zip; never modifies it. Standard library only.

Usage: pptx_to_markdown.py DECK.pptx [OUT.md]   (prints to stdout without OUT.md)
"""

import re
import sys
import zipfile
import xml.etree.ElementTree as ET

NS = {
    'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
    'p': 'http://schemas.openxmlformats.org/presentationml/2006/main',
    'r': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
}
REL = '{http://schemas.openxmlformats.org/package/2006/relationships}Relationship'
RID = '{%s}id' % NS['r']


def rels(z, path):
    d, _, f = path.rpartition('/')
    try:
        root = ET.fromstring(z.read(f'{d}/_rels/{f}.rels'))
    except KeyError:
        return {}
    return {r.get('Id'): (r.get('Type', '').rsplit('/', 1)[-1], r.get('Target', ''), r.get('TargetMode')) for r in root.iter(REL)}


def para_text(p, links):
    out = []
    for node in p:
        tag = node.tag.rsplit('}', 1)[-1]
        if tag in ('r', 'fld'):
            t = ''.join(x.text or '' for x in node.findall('a:t', NS))
            link = node.find('a:rPr/a:hlinkClick', NS)
            if link is not None and links.get(link.get(RID), ('', '', ''))[2] == 'External' and t.strip():
                url = links[link.get(RID)][1]
                t = t if url in t or url.removeprefix('mailto:') in t else f'{t} ({url})'
            out.append(t)
        elif tag == 'br':
            out.append(' ')
    return re.sub(r'\s+', ' ', ''.join(out)).strip()


def frame_lines(txbody, links):
    lines = []
    for p in txbody.findall('a:p', NS):
        t = para_text(p, links)
        if not t:
            continue
        lvl = int(p.find('a:pPr', NS).get('lvl', 0)) if p.find('a:pPr', NS) is not None else 0
        bulleted = p.find('a:pPr/a:buNone', NS) is None and (p.find('a:pPr/a:buChar', NS) is not None or p.find('a:pPr/a:buAutoNum', NS) is not None or lvl)
        lines.append(('  ' * lvl + '- ' if bulleted else '') + t)
    return lines


def table_md(tbl, links):
    rows = []
    for tr in tbl.findall('a:tr', NS):
        rows.append([' / '.join(frame_lines(tc.find('a:txBody', NS), links)).replace('|', '\\|') if tc.find('a:txBody', NS) is not None else '' for tc in tr.findall('a:tc', NS)])
    if not rows:
        return []
    width = max(len(r) for r in rows)
    rows = [r + [''] * (width - len(r)) for r in rows]
    out = ['| ' + ' | '.join(rows[0]) + ' |', '|' + '---|' * width]
    out += ['| ' + ' | '.join(r) + ' |' for r in rows[1:]]
    return out + ['']


def walk(tree, links, out, imgs):
    for el in tree:
        tag = el.tag.rsplit('}', 1)[-1]
        if tag == 'sp':
            tb = el.find('p:txBody', NS)
            if tb is not None:
                lines = frame_lines(tb, links)
                if lines:
                    out.extend(lines + [''])
        elif tag == 'graphicFrame':
            tbl = el.find('.//a:tbl', NS)
            if tbl is not None:
                out.extend(table_md(tbl, links))
        elif tag == 'grpSp':
            walk(el, links, out, imgs)
        elif tag == 'pic':
            c = el.find('p:nvPicPr/p:cNvPr', NS)
            alt = (c.get('descr') or '').strip() if c is not None else ''
            if alt:
                imgs.append(alt)


def convert(path):
    z = zipfile.ZipFile(path)
    pres = ET.fromstring(z.read('ppt/presentation.xml'))
    prels = rels(z, 'ppt/presentation.xml')
    slides = [prels[s.get(RID)][1] for s in pres.findall('.//p:sldId', NS)]
    md = []
    for n, target in enumerate(slides, 1):
        spath = 'ppt/' + target.removeprefix('/ppt/').removeprefix('../')
        srels = rels(z, spath)
        root = ET.fromstring(z.read(spath))
        out, imgs = [], []
        walk(root.find('p:cSld/p:spTree', NS), srels, out, imgs)
        out = [l for l in out if not re.fullmatch(r'\d{1,2}', l.strip())]  # slide-number badges
        title = next((l for l in out if l.strip()), '')
        md += [f'## Slide {n}: {title.lstrip("- ")[:90]}', '']
        body = out[1:] if out and out[0] == title else out
        md += body
        for alt in imgs:
            md += [f'_Image: {alt}_', '']
        for r in srels.values():
            if r[0] == 'notesSlide':
                nroot = ET.fromstring(z.read('ppt/' + r[1].removeprefix('../')))
                notes = []
                for sp in nroot.iterfind('.//p:sp', NS):
                    ph = sp.find('p:nvSpPr/p:nvPr/p:ph', NS)
                    if ph is not None and ph.get('type') == 'body' and sp.find('p:txBody', NS) is not None:
                        notes += frame_lines(sp.find('p:txBody', NS), {})
                if notes:
                    md += ['**Speaker notes:**', ''] + notes + ['']
    return '\n'.join(md).rstrip() + '\n'


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    text = convert(sys.argv[1])
    if len(sys.argv) > 2:
        open(sys.argv[2], 'w').write(text)
    else:
        sys.stdout.write(text)
