`marked-15.0.12.js` is the unmodified UMD build from the npm `marked@15.0.12`
package (MIT; see `marked-LICENSE.md`). No CDN is used at runtime.

Upstream: https://github.com/markedjs/marked/tree/v15.0.12

Archive: https://registry.npmjs.org/marked/-/marked-15.0.12.tgz

Verified npm archive integrity (SHA-512, base64):
`8dD6FusOQSrpv9Z1rdNMdlSgQOIP880DHqnohobOmYLElGEqAL/JvxvuxZO16r4HtjTlfPRDC1hbvxC9dPN2nA==`

Only the lexer is used. `../markdown.js` builds allowlisted DOM nodes, treats
HTML as text, suppresses images, and validates link URLs before assigning them.
When updating, verify the archive integrity and license, run the Markdown DOM
tests, and update the HTML and service worker asset versions together.
