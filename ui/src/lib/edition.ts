/** What an edition of the product adds to the core's pages without changing them.
 *
 *  The core registers nothing here, so every page is exactly the core's. An edition calls `extendEdition` once,
 *  from a module it loads at start, and the core's pages ask these hooks where an edition may differ. A hook is
 *  asked about one workspace at a time: an edition decides per workspace, the core never knows why. */
import type { ComponentType } from "react";
import type { Workspace } from "./app";

/** What the Files list knows when it asks an edition for something to draw above its folders. */
export type FilesTopProps = {
  ws: Workspace;
  folder: string | null;                                       // the folder being viewed; null at the top level
  folders: { id: string; name: string; path: string }[];       // every folder of the workspace
  editor: boolean;                                             // whether this member may change files here
};

export type Edition = {
  /** The name of a rail entry in this workspace, already in the reader's language; null keeps the core's own. */
  navLabel?: (key: string, ws: Workspace) => string | null | undefined;
  /** Drawn on the Files list, under the page head and above the folder trail. A component, so it may keep state and
   *  load what it needs like any other. */
  filesTop?: ComponentType<FilesTopProps>;
};

const hooks: Edition = {};

/** Adds an edition's hooks. A hook given again replaces the earlier one. */
export function extendEdition(e: Edition): void {
  Object.assign(hooks, e);
}

/** The edition's name for a rail entry and its pages in this workspace, or null where the core's own name stands. */
export function editionName(key: string, ws: Workspace): string | null {
  return hooks.navLabel?.(key, ws) || null;
}

/** What the edition draws above the Files list's folders, as a component; null in the core. */
export function filesTop(): ComponentType<FilesTopProps> | null {
  return hooks.filesTop || null;
}
