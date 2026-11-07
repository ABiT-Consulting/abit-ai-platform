import { describe, expect, it } from 'vitest';
import {
	generateCodeExample,
	listOperations,
	resolveSchema,
	schemaExample,
	schemaType,
	type OpenApiSchema
} from './developer';

const languages = ['curl', 'python', 'javascript'] as const;
const makeDocument = (operation: OpenApiSchema, method = 'post', path = '/api/items/'): OpenApiSchema => ({
	openapi: '3.1.0',
	paths: { [path]: { [method]: operation } }
});
const exampleFor = (
	document: OpenApiSchema,
	language: (typeof languages)[number] = 'curl',
	baseUrl = 'https://abit.ai'
) => generateCodeExample(listOperations(document)[0], document, baseUrl, language);

describe('OpenAPI operation inventory', () => {
	it('includes HEAD and OPTIONS, ignores path metadata, and merges operation parameter overrides', () => {
		const document = {
			components: { parameters: { ItemId: { name: 'item_id', in: 'path', required: true, schema: { type: 'string' } } } },
			paths: {
				'/api/items/{item_id}': {
					summary: 'Not an operation',
					parameters: [{ $ref: '#/components/parameters/ItemId' }, { name: 'page', in: 'query', schema: { default: 1 } }],
					get: {
						summary: 'Read items',
						operationId: 'same-id',
						tags: ['Items', 'Secondary'],
						parameters: [{ name: 'page', in: 'query', schema: { default: 3 } }]
					},
					head: { operationId: 'same-id' },
					options: {}
				}
			}
		};
		const operations = listOperations(document);
		expect(operations.map((operation) => operation.method).sort()).toEqual(['GET', 'HEAD', 'OPTIONS']);
		expect(new Set(operations.map((operation) => operation.id)).size).toBe(3);
		const get = operations.find((operation) => operation.method === 'GET')!;
		expect(get.group).toBe('Items');
		expect(get.summary).toBe('Read items');
		expect(get.parameters).toHaveLength(2);
		expect(get.parameters.find((parameter) => parameter.name === 'page')!.schema.default).toBe(3);
		expect(operations.find((operation) => operation.method === 'HEAD')!.group).toBe('other');
	});
});

describe('safe schema references and examples', () => {
	it('resolves escaped local pointers, keeps sibling descriptions, and leaves documents unchanged', () => {
		const document = { components: { schemas: { 'A/B~C': { type: 'string', description: 'Base' } } } };
		const before = JSON.stringify(document);
		expect(resolveSchema({ $ref: '#/components/schemas/A~1B~0C', description: 'Override' }, document)).toEqual({
			type: 'string',
			description: 'Override'
		});
		expect(JSON.stringify(document)).toBe(before);
	});

	it('does not follow remote references or prototype properties and bounds alias cycles', () => {
		const remote = { $ref: 'https://example.com/private-schema.json' };
		expect(resolveSchema(remote, {})).toEqual(remote);
		expect(resolveSchema({ $ref: '#/constructor/prototype' }, {})).toEqual({ $ref: '#/constructor/prototype' });
		const document = { components: { schemas: { A: { $ref: '#/components/schemas/B' }, B: { $ref: '#/components/schemas/A' } } } };
		expect(() => schemaExample({ $ref: '#/components/schemas/A' }, document)).not.toThrow();
		expect(schemaType({ $ref: '#/components/schemas/A' }, document)).toBe('any');
	});

	it('keeps required fields across allOf, respects explicit false/zero values, and omits read-only inputs', () => {
		const document = { components: { schemas: { Name: { type: 'object', required: ['name'], properties: { name: { type: 'string', example: 'Example name' } } } } } };
		const sample = schemaExample({
			allOf: [
				{ $ref: '#/components/schemas/Name' },
				{ type: 'object', required: ['enabled', 'count'], properties: { enabled: { type: 'boolean', default: false }, count: { type: 'integer', enum: [0, 1] } } }
			],
			type: 'object',
			properties: {
				local: { type: 'string', examples: ['sample'] },
				server_id: { type: 'string', readOnly: true }
			}
		}, document);
		expect(sample).toEqual({ name: 'Example name', enabled: false, count: 0, local: 'sample' });
	});

	it('chooses a usable nullable alternative and bounds recursive objects without circular output', () => {
		const document = {
			components: {
				schemas: {
					Node: {
						type: 'object',
						required: ['name', 'children'],
						properties: {
							name: { type: 'string' },
							children: { type: 'array', items: { $ref: '#/components/schemas/Node' } }
						}
					}
				}
			}
		};
		const sample = schemaExample({ $ref: '#/components/schemas/Node' }, document);
		expect(sample.name).toBe('string');
		expect(Array.isArray(sample.children)).toBe(true);
		expect(JSON.stringify(sample).length).toBeLessThan(1000);
		expect(schemaExample({ oneOf: [{ type: 'null' }, { type: 'string', enum: ['ready'] }] }, {})).toBe('ready');
		expect(schemaType({ type: 'array', items: { type: 'string' } }, {})).toBe('array<string>');
	});

	it('copies provided examples instead of sharing mutable values with the document', () => {
		const schema = { example: { nested: ['original'] } };
		const sample = schemaExample(schema, {});
		sample.nested[0] = 'changed';
		expect(schema.example.nested[0]).toBe('original');
	});
});

describe('code example URLs and authentication', () => {
	it('encodes path and query values, including repeated arrays and deep objects', () => {
		const document = makeDocument({
			'x-abit-access': 'authenticated',
			parameters: [
				{ name: 'item_id', in: 'path', required: true, example: "folder/blue sky'?" },
				{ name: 'q', in: 'query', example: 'a&b = c/? +' },
				{ name: 'tag', in: 'query', example: ['one & two', 'three'] },
				{ name: 'filter', in: 'query', style: 'deepObject', example: { state: 'open & pending' } }
			]
		}, 'get', '/api/items/{item_id}');
		const example = exampleFor(document);
		const url = new URL(example.match(/--url '([^']+)'/)![1]);
		expect(url.pathname).toBe('/api/items/folder%2Fblue%20sky%27%3F');
		expect(url.searchParams.get('q')).toBe('a&b = c/? +');
		expect(url.searchParams.getAll('tag')).toEqual(['one & two', 'three']);
		expect(url.searchParams.get('filter[state]')).toBe('open & pending');
	});

	it('uses the supplied application base and cannot switch origin using schema servers or network paths', () => {
		const document = makeDocument({ 'x-abit-access': 'authenticated' }, 'get', '//evil.example/api/items/');
		document.servers = [{ url: 'https://other.example' }];
		const example = exampleFor(document, 'curl', 'https://abit.ai/base/?ignored=true#ignored');
		const url = new URL(example.match(/--url '([^']+)'/)![1]);
		expect(url.origin).toBe('https://abit.ai');
		expect(url.pathname).toBe('/base/evil.example/api/items/');
		expect(url.search).toBe('');
		expect(() => exampleFor(document, 'curl', 'https://user:secret@abit.ai')).toThrow();
		expect(() => exampleFor(document, 'curl', 'javascript:alert(1)')).toThrow();
	});

	it.each(languages)('keeps authentication placeholders and schema credentials out of %s examples', (language) => {
		const document = makeDocument({
			'x-abit-access': 'admin',
			'x-abit-api-key-access': 'allowed',
			parameters: [{ name: 'api_key', in: 'query', example: 'never-copy-this-credential' }],
			requestBody: { content: { 'application/json': { schema: {
				type: 'object',
				properties: {
					password: { type: 'string', example: 'never-copy-this-password' },
					nested: { type: 'object', properties: { accessToken: { type: 'string', default: 'never-copy-this-token' } } }
				}
			} } } }
		});
		const example = exampleFor(document, language);
		expect(example).toContain('ABIT_API_KEY');
		expect(example).toContain('Administrator permissions');
		expect(example).not.toContain('never-copy-this');
		expect(example).not.toContain('localStorage');
		expect(example).not.toContain('document.cookie');
	});

	it.each(languages)('omits bearer auth for public routes and labels custom credentials in %s', (language) => {
		const publicExample = exampleFor(makeDocument({ 'x-abit-access': 'public' }, 'get'), language);
		expect(publicExample).not.toContain('Authorization');
		expect(publicExample).not.toContain('ABIT_API_KEY');
		const customExample = exampleFor(makeDocument({ 'x-abit-access': 'custom', 'x-abit-api-key-access': 'custom' }, 'get'), language);
		expect(customExample).toContain('<CUSTOM_CREDENTIALS>');
		expect(customExample).not.toContain('Bearer');
		expect(customExample).not.toContain('ABIT_API_KEY');
	});

	it('does not suggest API keys can bypass a disabled setting or route restriction', () => {
		for (const policy of ['disabled', 'restricted']) {
			const example = exampleFor(makeDocument({ 'x-abit-access': 'authenticated', 'x-abit-api-key-access': policy }, 'get'));
			expect(example).toContain('<AUTHORIZED_SESSION_TOKEN>');
			expect(example).not.toContain('$' + '{ABIT_API_KEY}');
		}
	});

	it('uses curl HEAD semantics for a HEAD operation', () => {
		const example = exampleFor(makeDocument({ 'x-abit-access': 'public' }, 'head'));
		expect(example).toContain('curl --head');
		expect(example).not.toContain('--request HEAD');
	});
});

describe('request body code examples', () => {
	it('quotes potentially hostile JSON sample strings as shell data and uses valid Python JSON literals', () => {
		const document = makeDocument({
			'x-abit-access': 'authenticated',
			requestBody: { content: { 'application/json': { example: {
				message: "x'$(touch /tmp/should-never-run)" + String.fromCharCode(96) + 'id' + String.fromCharCode(96),
				enabled: false,
				empty: null
			} } } }
		});
		const curl = exampleFor(document);
		expect(curl).toContain("'\"'\"'");
		expect(curl).toContain('--data-raw');
		const python = exampleFor(document, 'python');
		expect(python).toContain('payload = json.loads(');
		expect(python).toContain('json=payload');
		expect(() => new Function('return (async () => {\n' + exampleFor(document, 'javascript') + '\n});')).not.toThrow();
	});

	it.each(languages)('generates multipart uploads in %s without manually setting a boundary or reading text fields as files', (language) => {
		const document = makeDocument({
			'x-abit-access': 'authenticated',
			requestBody: { content: { 'multipart/form-data': { schema: {
				type: 'object',
				required: ['file', 'description'],
				properties: {
					file: { type: 'string', format: 'binary' },
					description: { type: 'string', example: '@/etc/passwd' }
				}
			} } } }
		});
		const example = exampleFor(document, language);
		expect(example).toContain('/path/to/file');
		expect(example).not.toContain('multipart/form-data');
		expect(example).not.toContain('boundary=');
		if (language === 'curl') {
			expect(example).toContain("--form 'file=@/path/to/file'");
			expect(example).toContain("--form-string 'description=@/etc/passwd'");
		} else if (language === 'python') {
			expect(example).toContain('with ExitStack() as stack:');
			expect(example).toContain('files=multipart_fields');
			expect(example).toContain('(None, "@/etc/passwd")');
		} else {
			expect(example).toContain('new FormData()');
			expect(example).toContain('new Blob(');
			expect(() => new Function('return (async () => {\n' + example + '\n});')).not.toThrow();
		}
	});

	it.each(languages)('generates form-urlencoded data with repeated array fields in %s', (language) => {
		const document = makeDocument({
			'x-abit-access': 'public',
			requestBody: { content: { 'application/x-www-form-urlencoded': { schema: {
				type: 'object',
				properties: {
					username: { type: 'string', example: 'user & guest' },
					scope: { type: 'array', example: ['read', 'write'] }
				}
			} } } }
		});
		const example = exampleFor(document, language);
		if (language === 'curl') {
			expect(example).toContain("--data-urlencode 'username=user & guest'");
			expect(example).toContain("--data-urlencode 'scope=read'");
			expect(example).toContain("--data-urlencode 'scope=write'");
		} else if (language === 'python') {
			expect(example).toContain('data=data');
			expect(example).not.toContain('json=payload');
		} else {
			expect(example).toContain('new URLSearchParams(');
			expect(() => new Function('return (async () => {\n' + example + '\n});')).not.toThrow();
		}
	});

	it('supports referenced request bodies and Pydantic binary schemas inside nullable arrays', () => {
		const document = makeDocument({
			'x-abit-access': 'authenticated',
			requestBody: { $ref: '#/components/requestBodies/Upload' }
		});
		document.components = {
			requestBodies: { Upload: { content: { 'multipart/form-data': { schema: { $ref: '#/components/schemas/Upload' } } } } },
			schemas: {
				Upload: { type: 'object', required: ['files'], properties: { files: { anyOf: [{ type: 'null' }, { type: 'array', items: { $ref: '#/components/schemas/File' } }] } } },
				File: { type: 'string', contentMediaType: 'application/octet-stream' }
			}
		};
		const before = JSON.stringify(document);
		expect(exampleFor(document)).toContain("--form 'files=@/path/to/file'");
		expect(JSON.stringify(document)).toBe(before);
	});
});
