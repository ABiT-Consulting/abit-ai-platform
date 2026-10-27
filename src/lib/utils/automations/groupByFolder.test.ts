import { describe, expect, it } from 'vitest';
import { groupByFolder } from './groupByFolder';

const folders = [
	{ id: 'b', name: 'Beta' },
	{ id: 'a', name: 'Alpha' },
	{ id: 'child', name: 'Tasks', parent_id: 'a' }
];

describe('automations grouped by folder', () => {
	it('sorts folders by name and places unfiled automations last', () => {
		const items = [
			{ id: '1', folder_id: null },
			{ id: '2', folder_id: 'b' },
			{ id: '3', folder_id: 'a' },
			{ id: '4', folder_id: 'b' }
		];
		const groups = groupByFolder(items, folders, 'No folder', 'Folder unavailable');
		expect(groups.map((g) => [g.name, g.items.map((i) => i.id)])).toEqual([
			['Alpha', ['3']],
			['Beta', ['2', '4']],
			['No folder', ['1']]
		]);
		expect(groups.map((g) => g.key)).toEqual(['folder:a', 'folder:b', 'unfiled']);
	});

	it('shows the path for nested folders', () => {
		const groups = groupByFolder([{ folder_id: 'child' }], folders, 'No folder', 'Missing');
		expect(groups[0].name).toBe('Alpha / Tasks');
	});

	it('keeps missing folder IDs separate and shows a fallback heading', () => {
		const groups = groupByFolder(
			[{ folder_id: 'missing-1' }, { folder_id: 'missing-2' }],
			folders,
			'No folder',
			'Folder unavailable'
		);
		expect(groups.map((g) => g.key)).toEqual(['folder:missing-1', 'folder:missing-2']);
		expect(groups.map((g) => g.name)).toEqual(['Folder unavailable', 'Folder unavailable']);
	});

	it('handles cycles in the folder hierarchy without looping', () => {
		const groups = groupByFolder(
			[{ folder_id: 'one' }],
			[
				{ id: 'one', name: 'One', parent_id: 'two' },
				{ id: 'two', name: 'Two', parent_id: 'one' }
			],
			'No folder',
			'Missing'
		);
		expect(groups[0].items).toHaveLength(1);
		expect(groups[0].name).toBe('Two / One');
	});

	it('does not mutate the filtered input or its order within a group', () => {
		const items = [
			{ id: 1, folder_id: 'b' },
			{ id: 2, folder_id: 'b' }
		];
		groupByFolder(items, folders, 'No folder', 'Missing');
		expect(items.map((item) => item.id)).toEqual([1, 2]);
	});
});
