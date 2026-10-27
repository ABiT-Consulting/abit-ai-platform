export type FolderEntry = { id: string; name: string; parent_id?: string | null };

export type AutomationGroup<T> = {
	key: string;
	name: string;
	items: T[];
	unfiled: boolean;
};

/** Group the current filtered page by destination folder, with unfiled automations last. */
export const groupByFolder = <T extends { folder_id?: string | null }>(
	items: T[],
	folderList: FolderEntry[],
	noFolderLabel: string,
	missingFolderLabel: string
): AutomationGroup<T>[] => {
	const folderMap = new Map(folderList.map((folder) => [folder.id, folder]));
	const groups = new Map<string, AutomationGroup<T>>();

	for (const automation of items) {
		const folderId = automation.folder_id;
		const key = folderId ? `folder:${folderId}` : 'unfiled';
		if (!groups.has(key)) {
			const names: string[] = [];
			const visited = new Set<string>();
			let folder = folderId ? folderMap.get(folderId) : undefined;
			while (folder && !visited.has(folder.id)) {
				names.unshift(folder.name);
				visited.add(folder.id);
				folder = folder.parent_id ? folderMap.get(folder.parent_id) : undefined;
			}
			groups.set(key, {
				key,
				name: folderId ? names.join(' / ') || missingFolderLabel : noFolderLabel,
				items: [],
				unfiled: !folderId
			});
		}
		groups.get(key)!.items.push(automation);
	}

	return [...groups.values()].sort((a, b) =>
		a.unfiled === b.unfiled
			? a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })
			: a.unfiled
				? 1
				: -1
	);
};
