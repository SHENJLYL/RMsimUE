import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { once } from 'node:events';
import { createServer } from 'node:net';
import { createInterface } from 'node:readline';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

import mqtt from 'mqtt';

const loopback = '127.0.0.1';
const randomSecret = () => randomBytes(24).toString('hex');

test('MQTT v3.1.1 team status and activation use only authenticated official station IDs', { timeout: 10000 }, async (t) => {
    const bridge = randomSecret();
    const redPassword = randomSecret();
    const bluePassword = randomSecret();
    const commands = [];
    const sessions = new Set();
    const send = (socket, packet) => socket.write(JSON.stringify({ v: 1, session: 'fixture', ...packet }) + '\n');
    const sendState = (socket, epoch, redStatus) => send(socket, {
        kind: 'rune_state', epoch,
        red: { rune_status: redStatus, activated_arms: 1, average_rings: 0 },
        blue: { rune_status: 1, activated_arms: 0, average_rings: 0 },
    });
    const ue = createServer((socket) => {
        sessions.add(socket);
        socket.on('close', () => sessions.delete(socket));
        createInterface({ input: socket }).on('line', (line) => {
            const packet = JSON.parse(line);
            if (packet.kind === 'hello') {
                assert.equal(packet.token, bridge);
                sendState(socket, 42, 2);
            } else {
                commands.push(packet);
            }
        });
    });
    t.after(() => {
        for (const socket of sessions) socket.destroy();
        ue.close();
    });
    ue.listen(0, loopback);
    await once(ue, 'listening');

    const available = createServer();
    available.listen(0, loopback);
    await once(available, 'listening');
    const mqttPort = available.address().port;
    await new Promise((resolve) => available.close(resolve));
    const child = spawn(process.execPath, [fileURLToPath(new URL('./sidecar.mjs', import.meta.url)),
        '--mqtt-port', String(mqttPort), '--ue-port', String(ue.address().port), '--operator', '3', '--operator', '104'], {
        env: { ...process.env, RMSIM_RUNE_BRIDGE_TOKEN: bridge, RMSIM_RUNE_OPERATOR_3_PASSWORD: redPassword,
            RMSIM_RUNE_OPERATOR_104_PASSWORD: bluePassword },
    });
    t.after(() => child.kill());
    let stderr = '';
    child.stderr.setEncoding('utf8').on('data', (data) => { stderr += data; });
    const lines = createInterface({ input: child.stdout });
    let ready = false;
    for await (const line of lines) {
        if (JSON.parse(line).event === 'match_ready') {
            ready = true;
            break;
        }
    }
    assert.equal(ready, true, stderr);

    function connect(clientId, username, password) {
        const client = mqtt.connect(`mqtt://${loopback}:${mqttPort}`, {
            clientId, protocolVersion: 4, username, password, reconnectPeriod: 0, clean: true,
        });
        t.after(() => client.end(true));
        return client;
    }

    await assert.rejects(once(connect('7', '7', redPassword), 'connect'), /not authorized|bad user name|bad username/i);
    await assert.rejects(once(connect('104', '3', redPassword), 'connect'), /not authorized|bad user name|bad username/i);
    const red = connect('3', '3', redPassword);
    const blue = connect('104', '104', bluePassword);
    async function sample(client) {
        await once(client, 'connect');
        const received = once(client, 'message');
        await client.subscribeAsync('RuneStatusSync', { qos: 1 });
        const [topic, bytes, packet] = await received;
        assert.equal(topic, 'RuneStatusSync');
        assert.equal(packet.qos, 1);
        return bytes.toString('hex');
    }
    assert.deepEqual(await Promise.all([sample(red), sample(blue)]), [
        '080210011d00000000', '080110001d00000000',
    ]);
    await red.publishAsync('RuneActivateCommand', Buffer.from([0x08, 0x00]), { qos: 1 });
    await red.publishAsync('RuneActivateCommand', Buffer.from([0x08, 0x01]), { qos: 1 });
    await new Promise((resolve) => setTimeout(resolve, 100));
    assert.deepEqual(commands, [{ v: 1, kind: 'rune_activate', session: 'fixture', epoch: 42, seq: 1, robot_id: 3, activate: 1 }]);
    const nextStatus = once(red, 'message');
    const [match] = sessions;
    send(match, { kind: 'rune_result', epoch: 43, seq: 1, result: 'stale_round' });
    sendState(match, 43, 1);
    const [, updated] = await nextStatus;
    assert.equal(updated.toString('hex'), '080110011d00000000');
    await red.publishAsync('RuneActivateCommand', Buffer.from([0x08, 0x01]), { qos: 1 });
    await new Promise((resolve) => setTimeout(resolve, 100));
    assert.deepEqual(commands[1], { v: 1, kind: 'rune_activate', session: 'fixture', epoch: 43, seq: 1, robot_id: 3, activate: 1 });
});
