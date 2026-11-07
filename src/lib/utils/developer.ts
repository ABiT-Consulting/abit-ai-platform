/**
 * Pure OpenAPI helpers for the in-app API documentation.
 * Examples never read a session, token, cookie, schema server URL, or remote reference.
 */
export type OpenApiSchema = Record<string, any>;

export type ApiOperation = {
	id: string;
	method: string;
	path: string;
	summary: string;
	group: string;
	operation: OpenApiSchema;
	parameters: OpenApiSchema[];
};

const METHODS = new Set(['get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace']);
const MAX_DEPTH = 7;
const own = (value: object, key: string) => Object.prototype.hasOwnProperty.call(value, key);
const record = (value: unknown): value is OpenApiSchema =>
	value !== null && typeof value === 'object' && !Array.isArray(value);

/** Resolve only local JSON pointers. Reference cycles and missing references stay bounded. */
export function resolveSchema(schema: unknown, document: OpenApiSchema): OpenApiSchema {
	let result = record(schema) ? schema : {};
	const visited = new Set<string>();
	for (let depth = 0; depth < 32 && typeof result.$ref === 'string'; depth += 1) {
		const ref = result.$ref;
		if (!ref.startsWith('#/') || visited.has(ref)) break;
		visited.add(ref);
		let target: unknown = document;
		try {
			for (const encoded of ref.slice(2).split('/')) {
				const key = decodeURIComponent(encoded).replace(/~1/g, '/').replace(/~0/g, '~');
				if (
					(!record(target) && !Array.isArray(target)) ||
					!own(target, key) ||
					['__proto__', 'prototype', 'constructor'].includes(key)
				) {
					target = undefined;
					break;
				}
				target = (target as OpenApiSchema)[key];
			}
		} catch {
			break;
		}
		if (!record(target)) break;
		const { $ref: _reference, ...siblings } = result;
		result = { ...target, ...siblings };
	}
	return result;
}

export function listOperations(document: OpenApiSchema): ApiOperation[] {
	const operations: ApiOperation[] = [];
	for (const [path, rawPathItem] of Object.entries(document.paths ?? {})) {
		const pathItem = resolveSchema(rawPathItem, document);
		for (const [method, rawOperation] of Object.entries(pathItem)) {
			if (!METHODS.has(method.toLowerCase()) || !record(rawOperation)) continue;
			const operation = resolveSchema(rawOperation, document);
			const parameters = new Map<string, OpenApiSchema>();
			for (const rawParameter of [
				...(Array.isArray(pathItem.parameters) ? pathItem.parameters : []),
				...(Array.isArray(operation.parameters) ? operation.parameters : [])
			]) {
				const parameter = resolveSchema(rawParameter, document);
				if (typeof parameter.name === 'string' && typeof parameter.in === 'string') {
					parameters.set(parameter.in + ':' + parameter.name, parameter);
				}
			}
			const upperMethod = method.toUpperCase();
			operations.push({
				id: upperMethod + ':' + path,
				method: upperMethod,
				path,
				summary: String(operation.summary || operation.operationId || upperMethod + ' ' + path),
				group: Array.isArray(operation.tags) && operation.tags.length ? String(operation.tags[0]) : 'other',
				operation,
				parameters: [...parameters.values()]
			});
		}
	}
	return operations.sort(
		(a, b) =>
			a.group.localeCompare(b.group) ||
			a.path.localeCompare(b.path) ||
			a.method.localeCompare(b.method)
	);
}

function copyExample(value: unknown, depth = 0, seen = new Set<object>()): any {
	if (value === null || typeof value !== 'object') return value;
	if (depth >= MAX_DEPTH || seen.has(value)) return undefined;
	const next = new Set(seen).add(value);
	if (Array.isArray(value)) {
		return value.map((item) => copyExample(item, depth + 1, next)).filter((item) => item !== undefined);
	}
	return Object.fromEntries(
		Object.entries(value)
			.map(([key, item]) => [key, copyExample(item, depth + 1, next)])
			.filter(([, item]) => item !== undefined)
	);
}

function explicitExample(schema: OpenApiSchema, document: OpenApiSchema): { found: boolean; value?: any } {
	if (own(schema, 'example')) return { found: true, value: schema.example };
	if (Array.isArray(schema.examples) && schema.examples.length) {
		return { found: true, value: schema.examples[0] };
	}
	if (record(schema.examples)) {
		for (const example of Object.values(schema.examples)) {
			const resolved = resolveSchema(example, document);
			if (own(resolved, 'value')) return { found: true, value: resolved.value };
		}
	}
	return { found: false };
}

function buildExample(
	input: unknown,
	document: OpenApiSchema,
	depth: number,
	seen: Set<object>
): any {
	if (depth >= MAX_DEPTH || (record(input) && seen.has(input))) return undefined;
	const schema = resolveSchema(input, document);
	const next = new Set(seen);
	if (record(input)) next.add(input);
	const provided = explicitExample(schema, document);
	if (provided.found) return copyExample(provided.value);
	if (own(schema, 'const')) return copyExample(schema.const);
	if (own(schema, 'default')) return copyExample(schema.default);
	if (Array.isArray(schema.enum) && schema.enum.length) return copyExample(schema.enum[0]);

	const { allOf, oneOf, anyOf, ...base } = schema;
	const baseHasShape = ['type', 'properties', 'items', 'additionalProperties'].some((key) => own(base, key));
	if (Array.isArray(allOf) && allOf.length) {
		const values = allOf
			.map((part) => buildExample(part, document, depth + 1, next))
			.filter((value) => value !== undefined);
		if (baseHasShape) values.push(buildExample(base, document, depth + 1, next));
		const defined = values.filter((value) => value !== undefined);
		return defined.length && defined.every(record)
			? Object.assign({}, ...defined)
			: defined[0] ?? null;
	}
	const alternatives = Array.isArray(oneOf) ? oneOf : Array.isArray(anyOf) ? anyOf : [];
	if (alternatives.length) {
		const selected = alternatives.find((part) => resolveSchema(part, document).type !== 'null') ?? alternatives[0];
		const value = buildExample(selected, document, depth + 1, next);
		const baseValue = baseHasShape ? buildExample(base, document, depth + 1, next) : undefined;
		return record(value) && record(baseValue) ? { ...value, ...baseValue } : value;
	}

	const types = Array.isArray(schema.type) ? schema.type : [schema.type];
	const type = types.find((item) => item && item !== 'null') ?? types[0];
	if (type === 'null') return null;
	if (type === 'object' || record(schema.properties) || own(schema, 'additionalProperties')) {
		const properties = record(schema.properties) ? schema.properties : {};
		const required = Array.isArray(schema.required)
			? schema.required.filter((key: unknown): key is string => typeof key === 'string')
			: [];
		const entries: [string, any][] = [];
		for (const key of new Set<string>([...required, ...Object.keys(properties)])) {
			const property = resolveSchema(properties[key], document);
			if (property.readOnly) continue;
			const value = buildExample(properties[key] ?? {}, document, depth + 1, next);
			if (value !== undefined) entries.push([key, value]);
		}
		if (!entries.length && schema.additionalProperties) {
			const value = buildExample(schema.additionalProperties, document, depth + 1, next);
			if (value !== undefined) entries.push(['key', value]);
		}
		return Object.fromEntries(entries);
	}
	if (type === 'array' || schema.items) {
		const value = buildExample(schema.items ?? {}, document, depth + 1, next);
		return value === undefined ? [] : [value];
	}
	if (type === 'boolean') return true;
	if (type === 'integer' || type === 'number') {
		let value = typeof schema.minimum === 'number' ? schema.minimum : 1;
		if (typeof schema.exclusiveMinimum === 'number') value = schema.exclusiveMinimum + 1;
		else if (schema.exclusiveMinimum === true) value += 1;
		if (typeof schema.maximum === 'number') value = Math.min(value, schema.maximum);
		return type === 'integer' ? Math.ceil(value) : value;
	}
	if (type === 'string') {
		const examples: Record<string, string> = {
			binary: '<file>',
			byte: '<base64>',
			password: '<password>',
			email: 'user@example.com',
			uuid: '00000000-0000-4000-8000-000000000000',
			date: '2026-01-01',
			'date-time': '2026-01-01T00:00:00Z',
			uri: 'https://example.com',
			url: 'https://example.com'
		};
		return examples[schema.format] ?? 'string';
	}
	return 'value';
}

/** Request-oriented examples include required fields and omit read-only properties. */
export function schemaExample(schema: unknown, document: OpenApiSchema, depth = 0): any {
	return buildExample(schema, document, Math.max(0, depth), new Set()) ?? null;
}

export function schemaType(schema: unknown, document: OpenApiSchema): string {
	const visit = (input: unknown, depth: number): string => {
		if (depth >= MAX_DEPTH) return '…';
		const value = resolveSchema(input, document);
		for (const [key, separator] of [['oneOf', ' | '], ['anyOf', ' | '], ['allOf', ' & ']]) {
			if (Array.isArray(value[key]) && value[key].length) {
				return [...new Set(value[key].map((item: unknown) => visit(item, depth + 1)))].join(separator);
			}
		}
		if (Array.isArray(value.type)) return value.type.join(' | ');
		if (value.type === 'array' || value.items) return 'array<' + visit(value.items ?? {}, depth + 1) + '>';
		if (typeof value.type === 'string') {
			return value.type + (value.format ? ' (' + value.format + ')' : '');
		}
		if (value.properties || value.additionalProperties) return 'object';
		if (Array.isArray(value.enum) && value.enum.length) {
			return [...new Set(value.enum.map((item: unknown) => item === null ? 'null' : typeof item))].join(' | ');
		}
		return 'any';
	};
	return visit(schema, 0);
}

function sensitiveName(name: string): boolean {
	const normalized = name.replace(/([a-z0-9])([A-Z])/g, '$1_$2');
	return /(?:^|[_-])(?:password|passwd|secrets?|api[_-]?keys?|access[_-]?token|refresh[_-]?token|bearer[_-]?token|token|authorization|session[_-]?(?:token|cookie)|cookies?|client[_-]?secret)$/i.test(normalized);
}

function safeExample(value: any, name = '', depth = 0): any {
	if (depth >= MAX_DEPTH) return null;
	if (Array.isArray(value)) return value.map((item) => safeExample(item, name, depth + 1));
	if (record(value)) {
		return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, safeExample(item, key, depth + 1)]));
	}
	return sensitiveName(name) ? '<' + name + '>' : value;
}

function parameterExample(parameter: OpenApiSchema, document: OpenApiSchema): any {
	if (sensitiveName(parameter.name) || resolveSchema(parameter.schema, document).format === 'password') {
		return '<' + parameter.name + '>';
	}
	const explicit = explicitExample(parameter, document);
	return safeExample(explicit.found ? explicit.value : schemaExample(parameter.schema ?? {}, document), parameter.name);
}

const scalar = (value: any): string =>
	value === undefined || value === null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
const percent = (value: any) =>
	encodeURIComponent(scalar(value)).replace(/[!'()*]/g, (char) => '%' + char.charCodeAt(0).toString(16).toUpperCase());

function pathValue(value: any, parameter: OpenApiSchema): string {
	const style = parameter.style ?? 'simple';
	const explode = parameter.explode === true;
	const name = percent(parameter.name);
	const values = Array.isArray(value) ? value.map(percent) : null;
	const entries = record(value) ? Object.entries(value).map(([key, item]) => [percent(key), percent(item)]) : null;
	if (style === 'matrix') {
		if (values) return explode ? values.map((item) => ';' + name + '=' + item).join('') : ';' + name + '=' + values.join(',');
		if (entries) return explode ? entries.map(([key, item]) => ';' + key + '=' + item).join('') : ';' + name + '=' + entries.flat().join(',');
		return ';' + name + '=' + percent(value);
	}
	const delimiter = style === 'label' && explode ? '.' : ',';
	const content = values
		? values.join(delimiter)
		: entries
			? (explode ? entries.map(([key, item]) => key + '=' + item) : entries.flat()).join(delimiter)
			: percent(value);
	return (style === 'label' ? '.' : '') + content;
}

function appendQuery(query: URLSearchParams, parameter: OpenApiSchema, value: any) {
	const name = parameter.name;
	const style = parameter.style ?? 'form';
	const explode = parameter.explode ?? style === 'form';
	if (Array.isArray(value)) {
		if (style === 'form' && explode) value.forEach((item) => query.append(name, scalar(item)));
		else query.append(name, value.map(scalar).join(style === 'spaceDelimited' ? ' ' : style === 'pipeDelimited' ? '|' : ','));
	} else if (record(value)) {
		const entries = Object.entries(value);
		if (style === 'deepObject') entries.forEach(([key, item]) => query.append(name + '[' + key + ']', scalar(item)));
		else if (explode) entries.forEach(([key, item]) => query.append(key, scalar(item)));
		else query.append(name, entries.flatMap(([key, item]) => [key, scalar(item)]).join(','));
	} else {
		query.append(name, scalar(value));
	}
}

function objectProperties(input: unknown, document: OpenApiSchema, depth = 0): OpenApiSchema {
	if (depth >= MAX_DEPTH) return {};
	const schema = resolveSchema(input, document);
	const alternatives = Array.isArray(schema.oneOf) ? schema.oneOf : Array.isArray(schema.anyOf) ? schema.anyOf : [];
	const selected = alternatives.find((part: unknown) => resolveSchema(part, document).type !== 'null');
	const inherited = (Array.isArray(schema.allOf) ? schema.allOf : []).map((part: unknown) => objectProperties(part, document, depth + 1));
	if (selected) inherited.push(objectProperties(selected, document, depth + 1));
	return Object.assign({}, ...inherited, schema.properties ?? {});
}

function isFile(input: unknown, document: OpenApiSchema, depth = 0): boolean {
	if (depth >= MAX_DEPTH) return false;
	const schema = resolveSchema(input, document);
	if (schema.format === 'binary' || schema.contentMediaType === 'application/octet-stream') return true;
	if (schema.items && isFile(schema.items, document, depth + 1)) return true;
	return ['oneOf', 'anyOf', 'allOf'].some((key) =>
		Array.isArray(schema[key]) && schema[key].some((part: unknown) => isFile(part, document, depth + 1))
	);
}

type FormField = { name: string; value: string; file: boolean };
type ExampleBody = { media: string; value: any; fields: FormField[]; binary: boolean };

function requestBody(operation: OpenApiSchema, document: OpenApiSchema): ExampleBody | undefined {
	const body = resolveSchema(operation.requestBody, document);
	const content = record(body.content) ? body.content : {};
	const media = ['application/json', 'multipart/form-data', 'application/x-www-form-urlencoded'].find((key) => own(content, key))
		?? Object.keys(content).find((key) => key.endsWith('+json'))
		?? Object.keys(content)[0];
	if (!media) return undefined;
	const definition = resolveSchema(content[media], document);
	const provided = explicitExample(definition, document);
	const value = safeExample(provided.found ? provided.value : schemaExample(definition.schema, document));
	const properties = objectProperties(definition.schema, document);
	const fields: FormField[] = [];
	for (const [name, item] of Object.entries(record(value) ? value : { data: value })) {
		if (isFile(properties[name], document)) {
			fields.push({ name, value: '/path/to/file', file: true });
		} else {
			for (const entry of Array.isArray(item) ? item : [item]) fields.push({ name, value: scalar(entry), file: false });
		}
	}
	return { media, value, fields, binary: isFile(definition.schema, document) };
}

function shellQuote(value: string): string {
	return "'" + value.replace(/'/g, "'\"'\"'") + "'";
}

function literal(value: any): string {
	return JSON.stringify(value).replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
}

/** Generate copyable examples only. This function never makes a request. */
export function generateCodeExample(
	op: ApiOperation,
	document: OpenApiSchema,
	baseUrl: string,
	language: 'curl' | 'python' | 'javascript'
): string {
	const base = new URL(baseUrl);
	if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password) {
		throw new Error('The API base URL must be an HTTP(S) application URL without credentials.');
	}
	const parameters = op.parameters.map((parameter) => resolveSchema(parameter, document));
	let path = op.path;
	for (const parameter of parameters.filter((item) => item.in === 'path')) {
		path = path.split('{' + parameter.name + '}').join(pathValue(parameterExample(parameter, document), parameter));
	}
	path = path.replace(/\{([^}]+)\}/g, (_match, name) => percent('<' + name + '>'));
	const url = new URL(base.origin + base.pathname.replace(/\/+$/, '') + '/' + path.replace(/^\/+/, ''));
	for (const parameter of parameters.filter((item) => item.in === 'query')) {
		const value = parameterExample(parameter, document);
		if (value !== null || parameter.required) appendQuery(url.searchParams, parameter, value);
	}

	const access = op.operation['x-abit-access'] ??
		(Array.isArray(op.operation.security) && !op.operation.security.length ? 'public' : 'authenticated');
	const keyAccess = op.operation['x-abit-api-key-access'] ?? 'allowed';
	const notes: string[] = ['Replace sample values before running this request.'];
	const headers: Record<string, string> = { Accept: 'application/json' };
	let apiKey = false;
	if (access === 'custom' || keyAccess === 'custom') {
		notes.push('Custom authentication: supply <CUSTOM_CREDENTIALS> as required by this endpoint.');
	} else if (access !== 'public') {
		if (keyAccess === 'disabled' || keyAccess === 'restricted') {
			notes.push(keyAccess === 'disabled'
				? 'API-key access is unavailable for this account. This example requires your own authorized session bearer token.'
				: 'This endpoint is outside the API-key allowlist. Use an authorized session bearer token.');
			headers.Authorization = 'Bearer <AUTHORIZED_SESSION_TOKEN>';
		} else {
			apiKey = true;
			notes.push('Set ABIT_API_KEY in your environment; never put a real key in shared example code.');
			if (keyAccess === 'conditional') notes.push('API-key access depends on allowed resource paths and your account permissions.');
		}
		if (access === 'admin') notes.push('Administrator permissions are required.');
	}
	for (const parameter of parameters.filter((item) => item.in === 'header')) {
		if (/^(authorization|cookie|host|content-length|content-type|connection)$/i.test(parameter.name) || /[\r\n]/.test(parameter.name)) continue;
		const value = scalar(parameterExample(parameter, document));
		if (!/[\r\n]/.test(value)) headers[parameter.name] = value;
	}

	const body = requestBody(op.operation, document);
	const jsonBody = body && (body.media === 'application/json' || body.media.endsWith('+json'));
	const multipart = body?.media === 'multipart/form-data';
	const formEncoded = body?.media === 'application/x-www-form-urlencoded';
	if (body && !multipart && !formEncoded) headers['Content-Type'] = body.media;
	const fields = body?.fields ?? [];

	if (language === 'curl') {
		const lines = [op.method === 'HEAD' ? 'curl --head' : 'curl --request ' + op.method];
		lines.push('  --url ' + shellQuote(url.toString()));
		if (apiKey) lines.push('  --header "Authorization: Bearer $' + '{ABIT_API_KEY}"');
		for (const [key, value] of Object.entries(headers)) lines.push('  --header ' + shellQuote(key + ': ' + value));
		if (multipart) {
			for (const field of fields) lines.push('  ' + (field.file ? '--form ' : '--form-string ') + shellQuote(field.name + '=' + (field.file ? '@' : '') + field.value));
		} else if (formEncoded) {
			for (const field of fields) lines.push('  --data-urlencode ' + shellQuote(field.name + '=' + field.value));
		} else if (body) {
			lines.push('  ' + (body.binary ? '--data-binary ' + shellQuote('@/path/to/file') : '--data-raw ' + shellQuote(jsonBody ? JSON.stringify(body.value) : scalar(body.value))));
		}
		return notes.map((note) => '# ' + note).join('\n') + '\n' + lines.join(' \\\n');
	}

	if (language === 'python') {
		const lines = notes.map((note) => '# ' + note);
		lines.push('import json');
		if (apiKey) lines.push('import os');
		if (body?.binary || (multipart && fields.some((field) => field.file))) lines.push('from contextlib import ExitStack');
		lines.push('import requests', '', 'url = ' + literal(url.toString()), 'headers = json.loads(' + literal(JSON.stringify(headers)) + ')');
		if (apiKey) lines.push('headers["Authorization"] = "Bearer " + os.environ["ABIT_API_KEY"]');
		const argumentsList = [literal(op.method), 'url', 'headers=headers'];
		let indent = '';
		if (multipart) {
			if (fields.some((field) => field.file)) {
				lines.push('with ExitStack() as stack:');
				indent = '    ';
			}
			const entries = fields.map((field) =>
				'(' + literal(field.name) + ', ' + (field.file
					? '("upload.bin", stack.enter_context(open("/path/to/file", "rb")))'
					: '(None, ' + literal(field.value) + ')') + ')'
			);
			lines.push(indent + 'multipart_fields = [' + entries.join(', ') + ']');
			argumentsList.push('files=multipart_fields');
		} else if (formEncoded) {
			lines.push('data = json.loads(' + literal(JSON.stringify(fields.map((field) => [field.name, field.value]))) + ')');
			argumentsList.push('data=data');
		} else if (body?.binary) {
			lines.push('with ExitStack() as stack:', '    payload = stack.enter_context(open("/path/to/file", "rb"))');
			indent = '    ';
			argumentsList.push('data=payload');
		} else if (body) {
			lines.push('payload = ' + (jsonBody ? 'json.loads(' + literal(JSON.stringify(body.value)) + ')' : literal(scalar(body.value))));
			argumentsList.push((jsonBody ? 'json=payload' : 'data=payload'));
		}
		argumentsList.push('timeout=30');
		lines.push(indent + 'response = requests.request(' + argumentsList.join(', ') + ')', 'response.raise_for_status()', 'print(response.text)');
		return lines.join('\n');
	}

	const lines = ['// Node.js 18+.', ...notes.map((note) => '// ' + note)];
	if (body?.binary || (multipart && fields.some((field) => field.file))) lines.push('const { readFile } = await import("node:fs/promises");');
	lines.push('const url = ' + literal(url.toString()) + ';', 'const headers = ' + literal(headers) + ';');
	if (apiKey) {
		lines.push('if (!process.env.ABIT_API_KEY) throw new Error("Set ABIT_API_KEY first.");', 'headers.Authorization = "Bearer " + process.env.ABIT_API_KEY;');
	}
	const options = ['method: ' + literal(op.method), 'headers'];
	if (multipart) {
		lines.push('const body = new FormData();');
		for (const field of fields) {
			lines.push(field.file
				? 'body.append(' + literal(field.name) + ', new Blob([await readFile("/path/to/file")]), "upload.bin");'
				: 'body.append(' + literal(field.name) + ', ' + literal(field.value) + ');');
		}
		options.push('body');
	} else if (formEncoded) {
		lines.push('const body = new URLSearchParams(' + literal(fields.map((field) => [field.name, field.value])) + ');');
		options.push('body');
	} else if (body?.binary) {
		lines.push('const body = await readFile("/path/to/file");');
		options.push('body');
	} else if (body) {
		lines.push('const body = ' + (jsonBody ? 'JSON.stringify(JSON.parse(' + literal(JSON.stringify(body.value)) + '))' : literal(scalar(body.value))) + ';');
		options.push('body');
	}
	lines.push('const response = await fetch(url, { ' + options.join(', ') + ' });', 'if (!response.ok) throw new Error("HTTP " + response.status + ": " + await response.text());', 'console.log(await response.text());');
	return lines.join('\n');
}
