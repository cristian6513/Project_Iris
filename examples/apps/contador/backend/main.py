"""Example external backend. Standard library only; no Core or Hermes imports."""
import json
import os
import tempfile


def handle(action, args, context):
    target = context.data_dir / 'counter.json'
    state = json.loads(target.read_text(encoding='utf-8')) if target.exists() else {'count': 0}
    if action == 'increment':
        amount = args['amount']
        if not 1 <= amount <= 1000:
            raise ValueError('amount must be between 1 and 1000')
        state['count'] += amount
        descriptor, name = tempfile.mkstemp(prefix='counter-', suffix='.tmp', dir=context.data_dir)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
                json.dump(state, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(name, target)
        finally:
            if os.path.exists(name):
                os.unlink(name)
    elif action != 'status':
        raise ValueError('Unknown action')
    return state
