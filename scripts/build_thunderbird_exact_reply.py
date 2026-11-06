"""Prepare an optional exact-body reply adaptation; never install or alter prefs."""
import argparse
import difflib
import hashlib
import json
import zipfile
from pathlib import Path


def adapt(source):
    def replace_once(text,old,new):
        if text.count(old)!=1:
            raise ValueError('THUNDERBIRD_SOURCE_CONTRACT_CHANGED')
        return text.replace(old,new,1)
    source=replace_once(source,
        'function replyToMessage(messageId, folderPath, body, replyAll, isHtml, to, cc, bcc, from, attachments, skipReview, saveAsDraft) {',
        'function replyToMessage(messageId, folderPath, body, replyAll, isHtml, to, cc, bcc, from, attachments, skipReview, saveAsDraft, subject, includeQuoted = true) {')
    start=source.index('function replyToMessage(')
    finish=source.index('sendMessageDirectly(composeFields,',start)
    block=source[start:finish]
    guard='''if (subject !== undefined && (typeof subject !== "string" || /[\\r\\n]/.test(subject))) {
                            resolve({ error: "Reply subject must be a string without line breaks" });
                            return;
                          }
                          if (typeof includeQuoted !== "boolean" || (includeQuoted === false && !skipReview)) {
                            resolve({ error: "Exact reply bodies require direct mode and an explicit boolean includeQuoted" });
                            return;
                          }
                          '''
    block=replace_once(block,'if (skipReview && saveAsDraft) {',guard+'if (skipReview && saveAsDraft) {')
    block=replace_once(block,
        'composeFields.subject = /^re:/i.test(origSubject) ? origSubject : `Re: ${origSubject}`;',
        'composeFields.subject = subject === undefined ? (/^re:/i.test(origSubject) ? origSubject : `Re: ${origSubject}`) : subject;')
    block=replace_once(block,'if (replyUseHtml) {',
        '''if (includeQuoted === false) {
                            composeFields.body = replyUseHtml
                              ? `<html><head><meta charset="UTF-8"></head><body>${formatBodyHtml(body, isHtml)}</body></html>`
                              : (body || "");
                          } else if (replyUseHtml) {''')
    source=source[:start]+block+source[finish:]
    source=replace_once(source,
        'replyToMessage(args.messageId, args.folderPath, args.body, args.replyAll, args.isHtml, args.to, args.cc, args.bcc, args.from, args.attachments, args.skipReview, args.saveAsDraft)',
        'replyToMessage(args.messageId, args.folderPath, args.body, args.replyAll, args.isHtml, args.to, args.cc, args.bcc, args.from, args.attachments, args.skipReview, args.saveAsDraft, args.subject, args.includeQuoted)')
    start=source.index('name: "replyToMessage"')
    finish=source.index('name: "forwardMessage"',start)
    block=source[start:finish]
    marker='replyAll: { type: "boolean", description: "Reply to all recipients (default: false)" },'
    block=replace_once(block,marker,
        '''subject: { type: "string", description: "Exact reply subject; defaults to the native Re: subject" },
            includeQuoted: { type: "boolean", default: true, description: "Append the original quote; false preserves the exact supplied body in direct mode" },
            '''+marker)
    return source[:start]+block+source[finish:]


def build(source_xpi,output_dir):
    target=Path(output_dir)
    target.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(source_xpi) as package:
        original=package.read('mcp_server/api.js').decode()
        changed=adapt(original)
        (target/'api.exact-reply.js').write_text(changed)
        diff=''.join(difflib.unified_diff(original.splitlines(keepends=True),changed.splitlines(keepends=True),
                   fromfile='a/mcp_server/api.js',tofile='b/mcp_server/api.js'))
        (target/'exact-reply.patch').write_text(diff)
        manifest={'source_sha256':hashlib.sha256(Path(source_xpi).read_bytes()).hexdigest(),
                  'patched_api_sha256':hashlib.sha256(changed.encode()).hexdigest(),
                  'installed':False,'preferences_modified':False,
                  'capabilities':['replyToMessage.subject','replyToMessage.includeQuoted']}
        (target/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
    return manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-xpi',required=True)
    parser.add_argument('--output-dir',required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.source_xpi,args.output_dir),indent=2))


if __name__=='__main__':
    main()
