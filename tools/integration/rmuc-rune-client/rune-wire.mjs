import protobuf from 'protobufjs';

// RM2027 V1.0.0 §2.2, verified against Transistor's 46757616 protocol extraction.
const schema = protobuf.parse(`
    syntax = "proto3";
    message RuneActivateCommand {
        optional uint32 activate = 1;
    }
    message RuneStatusSync {
        optional uint32 rune_status = 1;
        optional uint32 activated_arms = 2;
        optional float average_rings = 3;
    }
`, { keepCase: true }).root;

export const activationTopic = 'RuneActivateCommand';
export const statusTopic = 'RuneStatusSync';
export const activation = schema.lookupType(activationTopic);
export const status = schema.lookupType(statusTopic);
export const teams = new Map([['3', 'red'], ['4', 'red'], ['103', 'blue'], ['104', 'blue']]);
