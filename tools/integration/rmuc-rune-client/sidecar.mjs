import { createHash, timingSafeEqual } from 'node:crypto';
import { createConnection, createServer } from 'node:net';
import { parseArgs } from 'node:util';

import { Aedes } from 'aedes';

import { activation, activationTopic, status, statusTopic, teams } from './rune-wire.mjs';

const loopback = '127.0.0.1';
const stateTimeoutMs = 2500;
const socketLineLimit = 4096;
const subscribePrefix = '__rmsim_rune/';
const { values } = parseArgs({
    options: {
        'mqtt-port': { type: 'string', default: '3333' },
        'ue-port': { type: 'string', default: '9461' },
        operator: { type: 'string', multiple: true },
    },
});

function port(value) {
    if (!/^\d+$/.test(value) || Number(value) < 1024 || Number(value) > 65535) {
        throw new Error('Ports must be integers from 1024 to 65535.');
    }
    return Number(value);
}

function secret(name, maximum = 256) {
    const value = process.env[name];
    if (!value || value.length < 16 || value.length > maximum || !/^[\x21-\x7e]+$/.test(value)) {
        throw new Error(`${name} must contain 16–${maximum} printable ASCII characters without spaces.`);
    }
    return value;
}

function digest(value) {
    return createHash('sha256').update(value).digest();
}

const mqttPort = port(values['mqtt-port']);
const uePort = port(values['ue-port']);
if (mqttPort === uePort) throw new Error('The MQTT and private UE ports must differ.');
const allowed = values.operator ?? [];
if (allowed.length === 0 || new Set(allowed).size !== allowed.length || allowed.some((id) => !teams.has(id))) {
    throw new Error('Configure at least one unique --operator from 3, 4, 103, 104.');
}
const credentials = new Map(allowed.map((id) => [id, digest(secret(`RMSIM_RUNE_OPERATOR_${id}_PASSWORD`))]));
const bridgeToken = secret('RMSIM_RUNE_BRIDGE_TOKEN', 128);
const lastRequest = new Map();
let lastMatch;
let sequence = 0;
let ue;
let reconnect;
let stopping = false;

function log(event, details = {}) {
    process.stdout.write(JSON.stringify({ event, ...details }) + '\n');
}

const broker = await Aedes.createBroker({
    drainTimeout: 1500,
    preConnect(_client, packet, done) {
        done(null, packet.protocolVersion === 4 && packet.clean && !packet.will);
    },
    authenticate(client, username, password, done) {
        const expected = credentials.get(client.id);
        done(null, Boolean(expected && username === client.id && password &&
            password.length <= 256 && timingSafeEqual(digest(password), expected)));
    },
    authorizePublish(client, packet, done) {
        const permitted = client && credentials.has(client.id) && packet.topic === activationTopic &&
            packet.qos === 1 && !packet.retain && packet.payload.length <= 64;
        done(permitted ? null : new Error('unsupported_rune_publish'));
    },
    authorizeSubscribe(client, subscription, done) {
        if (!credentials.has(client.id) || subscription.topic !== statusTopic) {
            done(null, null);
            return;
        }
        done(null, { ...subscription, topic: subscribePrefix + teams.get(client.id) });
    },
    authorizeForward(client, packet) {
        if (!credentials.has(client.id) || packet.topic !== subscribePrefix + teams.get(client.id)) return null;
        return { ...packet, topic: statusTopic };
    },
});

function freshState() {
    return lastMatch && ue && !ue.destroyed && performance.now() - lastMatch.received <= stateTimeoutMs;
}

broker.on('publish', (packet, client) => {
    if (!client || packet.topic !== activationTopic || packet.dup) return;
    const robot_id = Number(client.id);
    let request;
    try {
        request = activation.decode(packet.payload);
    } catch {
        log('request_rejected', { robot_id, reason: 'invalid_protobuf' });
        return;
    }
    if (request.activate !== 1) {
        log('request_rejected', { robot_id, reason: 'invalid_activate' });
        return;
    }
    const now = performance.now();
    if (now - (lastRequest.get(robot_id) ?? -Infinity) < 100) {
        log('request_rejected', { robot_id, reason: 'rate_limit' });
        return;
    }
    lastRequest.set(robot_id, now);
    if (!freshState() || ue.writableLength) {
        log('request_rejected', { robot_id, reason: 'bridge_unavailable' });
        return;
    }
    const command = {
        v: 1, kind: 'rune_activate', session: lastMatch.session, epoch: lastMatch.epoch,
        seq: ++sequence, robot_id, activate: 1,
    };
    ue.write(JSON.stringify(command) + '\n');
    log('request_forwarded', { robot_id, seq: command.seq });
});

function validStatus(value) {
    return value && [1, 2, 3].includes(value.rune_status) && Number.isInteger(value.activated_arms) &&
        value.activated_arms >= 0 && value.activated_arms <= 10 && Number.isFinite(value.average_rings) &&
        value.average_rings >= 0 && value.average_rings <= 10;
}

function receive(line) {
    const message = JSON.parse(line);
    if (!message || message.v !== 1 || typeof message.session !== 'string' ||
        !message.session || message.session.length > 128 ||
        !Number.isSafeInteger(message.epoch) || message.epoch < 0) throw new Error('invalid_ue_frame');
    if (message.kind === 'rune_state' && validStatus(message.red) && validStatus(message.blue)) {
        if (!lastMatch || message.session !== lastMatch.session || message.epoch !== lastMatch.epoch) {
            sequence = 0;
            log('match_ready', { session: message.session, epoch: message.epoch });
        }
        lastMatch = { ...message, received: performance.now() };
        return;
    }
    if (message.kind === 'rune_result' && lastMatch && message.session === lastMatch.session &&
        Number.isSafeInteger(message.seq) &&
        message.seq > 0 && message.seq <= sequence && typeof message.result === 'string' &&
        /^[a-z0-9_]{1,80}$/.test(message.result)) {
        if (message.epoch === lastMatch.epoch ||
            (message.epoch > lastMatch.epoch && message.result === 'stale_round')) {
            log('referee_result', { seq: message.seq, result: message.result });
            if (message.epoch !== lastMatch.epoch) lastMatch = undefined;
            return;
        }
    }
    throw new Error('invalid_ue_frame');
}

function connectToMatch() {
    const socket = createConnection({ host: loopback, port: uePort });
    ue = socket;
    let buffer = '';
    socket.setEncoding('utf8');
    socket.setNoDelay(true);
    socket.setTimeout(stateTimeoutMs, () => socket.destroy());
    socket.on('connect', () => {
        socket.write(JSON.stringify({ v: 1, kind: 'hello', token: bridgeToken }) + '\n');
    });
    socket.on('data', (text) => {
        buffer += text;
        try {
            let newline;
            while ((newline = buffer.indexOf('\n')) !== -1) {
                if (newline > socketLineLimit) throw new Error('oversize_ue_frame');
                receive(buffer.slice(0, newline));
                buffer = buffer.slice(newline + 1);
            }
            if (buffer.length > socketLineLimit) throw new Error('oversize_ue_frame');
        } catch {
            log('bridge_frame_rejected');
            socket.destroy();
        }
    });
    socket.on('error', () => socket.destroy());
    socket.on('close', () => {
        ue = undefined;
        lastMatch = undefined;
        if (!stopping) reconnect = setTimeout(connectToMatch, 500);
    });
}

const server = createServer(broker.handle);
server.maxConnections = 16;
await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(mqttPort, loopback, resolve);
});
log('mqtt_listening', { host: loopback, port: mqttPort, operator_ids: allowed });
connectToMatch();

const downlink = setInterval(() => {
    if (!freshState()) return;
    for (const team of new Set(allowed.map((id) => teams.get(id)))) {
        const payload = Buffer.from(status.encode(lastMatch[team]).finish());
        broker.publish({ cmd: 'publish', topic: subscribePrefix + team, payload, qos: 1, retain: false });
    }
}, 1000);

function close() {
    stopping = true;
    clearInterval(downlink);
    clearTimeout(reconnect);
    ue?.destroy();
    server.close();
    broker.close();
}

process.once('SIGINT', close);
process.once('SIGTERM', close);
