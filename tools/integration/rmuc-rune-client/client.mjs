import { parseArgs } from 'node:util';

import mqtt from 'mqtt';

import { activation, activationTopic, status, statusTopic } from './rune-wire.mjs';

const { values } = parseArgs({
    options: {
        id: { type: 'string', default: '3' },
        port: { type: 'string', default: '3333' },
        activate: { type: 'boolean', default: false },
        expect: { type: 'string' },
    },
});
const id = values.id;
const password = process.env[`RMSIM_RUNE_OPERATOR_${id}_PASSWORD`];
if (!password) throw new Error(`Set RMSIM_RUNE_OPERATOR_${id}_PASSWORD for the selected terminal.`);
if (values.expect && !['1', '2', '3'].includes(values.expect)) throw new Error('Expected status must be 1, 2, or 3.');
const client = mqtt.connect(`mqtt://127.0.0.1:${values.port}`, {
    protocolVersion: 4, clientId: id, username: id, password, clean: true, reconnectPeriod: 0,
});
let sent = false;
const timeout = setTimeout(() => finish(new Error('No matching RuneStatusSync was received.')), 6500);

function finish(error) {
    clearTimeout(timeout);
    if (error) {
        console.error(error.message);
        process.exitCode = 1;
    }
    client.end(true);
}

client.on('error', finish);
client.on('connect', () => client.subscribe(statusTopic, { qos: 1 }, (error, grants) => {
    if (error || grants?.[0]?.qos !== 1) finish(error ?? new Error('RuneStatusSync subscription was refused.'));
}));
client.on('message', (topic, bytes, packet) => {
    if (topic !== statusTopic || packet.qos !== 1) return;
    const message = status.decode(bytes);
    console.log(JSON.stringify({ topic, ...message }));
    if (values.activate && !sent) {
        sent = true;
        client.publish(activationTopic, Buffer.from(activation.encode({ activate: 1 }).finish()), { qos: 1 },
            (error) => { if (error) finish(error); });
        return;
    }
    if (!values.expect || message.rune_status === Number(values.expect)) finish();
});
