/**
 * The document editor, in a module of its own so it can be loaded on demand.
 *
 * Milkdown brings ProseMirror, remark and a CodeMirror instance for code blocks with it,
 * and none of that is needed to READ a document: it is needed the moment someone clicks
 * Edit, which most sessions never do. Kept behind its own module, a dynamic import puts
 * the whole editor and its stylesheets in a separate chunk rather than in the bundle
 * every visitor downloads.
 *
 * The stylesheets belong here too, for the same reason. They are Crepe's `frame` theme,
 * whose colours `index.css` remaps onto this app's tokens so the editor follows the
 * assistant's theme instead of shipping a second palette.
 */
import { Crepe } from "@milkdown/crepe";
import "@milkdown/crepe/theme/common/style.css";
import "@milkdown/crepe/theme/frame.css";

export { Crepe };

/** The editor type, for callers that hold an instance without importing the module. */
export type MarkdownEditor = Crepe;
