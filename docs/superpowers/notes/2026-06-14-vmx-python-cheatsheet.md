# VMx Python cheatsheet (2026-06-14)

Quick reference distilled from `vendor/vmx/langs/python/src/vmx/` for the M3
implementation. Captures the actual API surface so the M3 plan's assumptions
about `AggregateVM3`, `MessageHub`, `RelayCommand`, `DerivedProperty`, etc. can
be reconciled with reality before writing real VMs.

## 0. Imports

All public types are re-exported from the top-level `vmx` package:

```python
from vmx import (
    # lifecycle
    ConstructionStatus, StatusTransitionError,
    # components
    ComponentVM, ComponentVMOf, ComponentVMBuilder, ComponentVMOfBuilder,
    # composites
    CompositeVM, CompositeVMBuilder,
    # aggregates (1..6)
    AggregateVM1, AggregateVM2, AggregateVM3, AggregateVM4, AggregateVM5, AggregateVM6,
    AggregateVM1Builder, AggregateVM2Builder, AggregateVM3Builder,  # aliases for AggregateVMBuilderN
    AggregateVMBuilder1, AggregateVMBuilder2, AggregateVMBuilder3,
    # commands
    RelayCommand, RelayCommandOf,
    # messages + hub
    Message, MessageHub, MessageHubProto, RxDispatcher,
    NULL_MESSAGE_HUB, NULL_DISPATCHER,
    PropertyChangedMessage, ConstructionStatusChangedMessage,
    CollectionChangedEvent, CollectionChangedMessage,
    # properties
    DerivedProperty, from_one, from_two, from_three, from_four, from_five, from_sources, from_many,
    # capabilities (state helpers)
    ExpandableState, SearchableState,
    ISelectable, IFilterable, IExpandable, IConstructable, IDestructable, IReconstructable,
)

# Opt-in notifications subpackage (must be imported explicitly):
from vmx.notifications import (
    ConfirmationVM, NotificationVM, NotificationHub, INotificationHub,
    Notification, NotificationReaction, NotificationType, make_confirm,
)
```

`vmx.AggregateVM3Builder` is **identical** to `vmx.AggregateVMBuilder3` (both
names exported). The naming "AggregateVMBuilderN" is the underlying class.

## 1. The builder pattern (no direct VM subclassing)

VMx VMs are **not** subclassed — they are constructed via immutable fluent
builders. To make a custom VM with extra commands/properties, you wrap a
`ComponentVM` (or `CompositeVM` etc.) instance inside your own facade class.

### 1.1 ComponentVM

```python
from vmx import ComponentVM

vm = (
    ComponentVM.builder()
    .name("status_bar")
    .hint("status strip")
    .services(hub, dispatcher)             # required
    .on_construct(lambda: None)            # optional
    .on_destruct(lambda: None)             # optional
    .background(False)                     # default False
    .build()
)
# or use the convenience for tests/null wiring:
vm = ComponentVM.builder().name("toast").with_null_services().build()
```

`ComponentVM.builder()` returns a `ComponentVMBuilder`. `with_null_services()`
wires `NULL_MESSAGE_HUB` + `NULL_DISPATCHER` for tests.

### 1.2 ComponentVMOf[M] — modeled leaf with settable model

```python
from vmx import ComponentVMOf
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class ToastModel:
    text: str
    level: str

vm = (
    ComponentVMOf[ToastModel].builder()
    .name("toast")
    .model(ToastModel(text="hi", level="info"))    # REQUIRED
    .modeled_hinter(lambda m: m.text)               # optional
    .on_model_changed(lambda m: ...)                # optional
    .services(hub, dispatcher)
    .build()
)

vm.model                                  # current model
vm.model = ToastModel(text="new", level="warning")  # setter publishes PropertyChangedMessage("model")
```

### 1.3 CompositeVM[VM]

```python
from vmx import CompositeVM

children: list[ToastVM] = []

composite = (
    CompositeVM[ToastVM].builder()
    .name("toast_stack")
    .services(hub, dispatcher)
    .children(lambda: tuple(children))     # REQUIRED — factory, invoked on construct()
    .auto_construct_on_add(True)           # auto-construct children appended after composite construct
    .async_selection(False)
    .build()
)
composite.construct()
composite.append(more_child_vm)            # mid-life adds emit CollectionChangedEvent
```

Note: composites do NOT expose a `.with_null_services()` shortcut — you must
pass `NULL_MESSAGE_HUB`/`NULL_DISPATCHER` explicitly.

### 1.4 AggregateVM3 (and 1..6)

```python
from vmx import AggregateVM3, AggregateVMBuilder3
# NB: AggregateVM3 has no static .builder() method — you instantiate the builder class.

agg = (
    AggregateVMBuilder3[HintLegendVM, StatusBarVM, ToastStackVM]()
    .name("chrome")
    .services(hub, dispatcher)
    .component_1(lambda: build_hint_legend_vm())
    .component_2(lambda: build_status_bar_vm())
    .component_3(lambda: build_toast_stack_vm())
    .build()
)
agg.construct()
agg.component_1, agg.component_2, agg.component_3   # populated on construct()
```

The factories are invoked lazily on `_on_construct()`. On reconstruct, the
previous slot is `dispose()`d before being overwritten.

## 2. Lifecycle (synchronous, depth-first)

All lifecycle operations are **synchronous** in the default (non-background)
mode. There is no `await vm.construct()` — it returns when done.

| Method        | Allowed from                  | Effect                                           |
|---------------|-------------------------------|--------------------------------------------------|
| `construct()` | DESTRUCTED, CONSTRUCTED       | Cascades depth-first; CONSTRUCTED ⇒ no-op        |
| `destruct()`  | CONSTRUCTED, DESTRUCTED       | Cascades depth-first; DESTRUCTED ⇒ no-op         |
| `reconstruct()` | CONSTRUCTED                 | Atomically destruct + construct (4 messages)     |
| `dispose()`   | any                           | Terminal; idempotent; sync depth-first cascade   |

Statuses: `DESTRUCTED → CONSTRUCTING → CONSTRUCTED → DESTRUCTING → DESTRUCTED → DISPOSED`.

`CompositeVM._on_construct()` populates children then calls `construct()` on
each. `CompositeVM.dispose()` calls `dispose()` on each child first, then super.
`AggregateVMN.dispose()` calls `dispose()` on each component slot.

### 2.1 Background-aware lifecycle

`ComponentVM.builder().background(True)` makes construct/destruct schedule the
callback on the dispatcher's background scheduler. M3 does NOT need this — we
keep everything foreground/synchronous and own async work explicitly via
`asyncio.create_task` in our facade classes.

### 2.2 The async setup pattern

VMx's `construct()` is synchronous. For VMs that need to load initial state
from infra (e.g. probing the SSO cache), do **not** put async work inside
`on_construct`. Instead:

1. `construct()` sets up sync state only.
2. Expose a separate `async def setup()` on the facade VM that the parent
   awaits after construct.

For M3 most VMs don't need this — they react to messages on the hub instead.

## 3. MessageHub & custom messages

```python
from vmx import MessageHub, Message
hub: MessageHub[Message] = MessageHub()

# subscribe (returns a reactivex Disposable)
sub = hub.messages.subscribe(on_next=lambda msg: handle(msg))
# unsubscribe
sub.dispose()
# publish
hub.send(my_message)
# tear down (hub completes the underlying subject)
hub.dispose()
```

### 3.1 Custom message envelopes

A `Message` is a structural Protocol — anything with `sender_name: str` and
`sender_object: object` satisfies it. So our custom messages look like:

```python
from dataclasses import dataclass, field
from aws_tui.infra.connection_resolver import Connection
from aws_tui.infra.aws_session import TokenState

@dataclass(frozen=True, slots=True)
class ConnectionChangedMessage:
    connection: Connection
    auth_state: TokenState
    sender_name: str = "root"               # implements Message protocol

    @property
    def sender_object(self) -> object:
        return self
```

Subscribers filter by `isinstance(msg, ConnectionChangedMessage)`.

### 3.2 Subscriber resilience

Per `HUB-007`, subscriber exceptions are swallowed by the hub wrapper; one
raising handler does NOT terminate the stream for other subscribers.

## 4. Reactive properties & DerivedProperty

`ComponentVM` exposes:

- `property_changed: rx.Observable[str]` — emits property name on each
  `_raise_property_changed("name")` call. Use for INPC-equivalent binding.
- `is_current: bool` — selection flag set by parent composite.
- `status: ConstructionStatus`, `is_constructed: bool` — read-only.

For a custom property on a facade VM, mirror VMx's pattern:

```python
self._title: str = ""

@property
def title(self) -> str: return self._title

@title.setter
def title(self, value: str) -> None:
    if self._title == value: return
    self._title = value
    self._hub.send(PropertyChangedMessage.create(self, self._name, "title"))
```

### 4.1 DerivedProperty[T]

Computes a value from N source observables; cached, equality-guarded, emits
on `value_changed`:

```python
from vmx import DerivedProperty, from_two
from reactivex.subject import BehaviorSubject

conn_subj: BehaviorSubject[Connection] = BehaviorSubject(initial_conn)
auth_subj: BehaviorSubject[TokenState] = BehaviorSubject(TokenState.CONNECTED)

label_prop = from_two(
    conn_subj, auth_subj,
    transform=lambda c, a: f"{c.name} ({c.kind}) - {a.value}",
)
label_prop.value             # current cached value
label_prop.value_changed     # rx.Observable[str]
label_prop.dispose()         # tear down
```

Important: `DerivedProperty.value` raises `RuntimeError` if no source has
emitted yet — feed `BehaviorSubject` (carries an initial value) sources, NOT
plain `Subject`.

## 5. RelayCommand & RelayCommandOf

```python
from vmx import RelayCommand, RelayCommandOf
from reactivex.subject import Subject

trigger: Subject[object] = Subject()

cmd = (
    RelayCommand.builder()
    .predicate(lambda: self.can_open)
    .task(lambda: self.open())
    .triggers(trigger)                # additive across multiple .triggers() calls
    .build()
)
cmd.can_execute()                     # bool
cmd.execute()                         # invoke; gated on can_execute
cmd.can_execute_changed               # rx.Observable[None]; fires on every trigger emit
cmd.dispose()                         # idempotent; releases trigger subscriptions

# Parameterized:
cmd_p = (
    RelayCommandOf[str].builder()
    .predicate(lambda x: x is not None)
    .task(lambda x: handle(x))
    .build()
)
cmd_p.execute("foo")
```

Predicates that raise are treated as False; tasks that raise propagate.

## 6. Notifications subpackage (Task 7)

```python
from vmx.notifications import (
    ConfirmationVM, NotificationHub, INotificationHub,
    Notification, NotificationReaction, NotificationType, make_confirm,
)

# A NotificationHub aggregates pending notifications; subscribers can resolve them
# via APPROVE/REJECT reactions. The render-side ConfirmationVM wraps a
# Notification with .approve_command / .reject_command.
```

`make_confirm` is a helper for building confirm notifications. M3's
`ConfirmationVM` shim CAN simply wrap an `asyncio.Future[bool]` and not use
`vmx.notifications` at all — that's simpler and avoids pulling in the
notification-hub concept just for this one use case.

## 7. Capabilities and state helpers

- `ISelectable`, `IFilterable`, `IExpandable`, `ISearchable`, etc. are
  `abc.ABC`-style markers registered against built-in VM classes — they're
  conformance interfaces, not protocols you implement directly in M3.
- `SearchableState`, `ExpandableState` — reusable state objects you compose
  inside a VM facade. M3 only needs `SearchableState` (for command palette
  filter). Even that we can skip — a simple `_filter_text: str` setter plus
  derived `_filtered_entries` recompute is enough.

## 8. RxDispatcher

```python
RxDispatcher.immediate()              # both fg + bg are ImmediateScheduler — sync, for tests
RxDispatcher.asyncio(loop)            # AsyncIOScheduler fg + ThreadPoolScheduler bg
```

For unit tests, use `NULL_DISPATCHER` (cheaper) or `RxDispatcher.immediate()`.

## 9. Smoke verification (run by Task 1)

```bash
uv run python -c "
from vmx import ComponentVM, CompositeVM, AggregateVM3, AggregateVMBuilder3, NULL_MESSAGE_HUB, NULL_DISPATCHER

c = ComponentVM.builder().name('c').with_null_services().build()
c.construct(); assert c.is_constructed; c.dispose()

cp = (CompositeVM.builder().name('p').services(NULL_MESSAGE_HUB, NULL_DISPATCHER)
      .children(lambda: [ComponentVM.builder().name('k').with_null_services().build()]).build())
cp.construct(); assert cp.count == 1; cp.dispose()

agg = (AggregateVMBuilder3().name('a').services(NULL_MESSAGE_HUB, NULL_DISPATCHER)
       .component_1(lambda: ComponentVM.builder().name('1').with_null_services().build())
       .component_2(lambda: ComponentVM.builder().name('2').with_null_services().build())
       .component_3(lambda: ComponentVM.builder().name('3').with_null_services().build())
       .build())
agg.construct(); agg.dispose()
print('VMx smoke OK')
"
```

## 10. Implementation pattern for aws-tui custom VMs

Since VMx VMs aren't subclassable, our facade pattern is:

```python
class ToastStackVM:
    """Facade owning a VMx CompositeVM and exposing aws-tui semantics."""

    def __init__(self, *, hub: MessageHub[Message], dispatcher: Dispatcher) -> None:
        self._hub = hub
        self._dispatcher = dispatcher
        self._toasts: list[ToastVM] = []
        self._inner: CompositeVM[ComponentVMOf[ToastModel]] = (
            CompositeVM.builder()
            .name("toast_stack")
            .services(hub, dispatcher)
            .children(lambda: tuple(t._inner for t in self._toasts))
            .auto_construct_on_add(True)
            .build()
        )

    def construct(self) -> None: self._inner.construct()
    def destruct(self) -> None: self._inner.destruct()
    def dispose(self) -> None: self._inner.dispose()

    def raise_toast(self, model: ToastModel) -> ToastVM:
        toast = ToastVM(model, hub=self._hub, dispatcher=self._dispatcher)
        self._toasts.append(toast)
        self._inner.append(toast._inner)         # triggers CollectionChangedEvent + auto-construct
        return toast
```

This pattern repeats throughout M3:

- Hold the underlying VMx instance as `_inner`.
- Forward `construct/destruct/dispose`.
- Add aws-tui-specific commands as `RelayCommand` instances built in `__init__`.
- For reactive state, publish `PropertyChangedMessage` on the hub on every set.
- For derived state, use `DerivedProperty` over `BehaviorSubject` sources.

## 11. Plan deviations recorded

- `AggregateVM3` has **no static `.builder()`** — instantiate
  `AggregateVMBuilder3()` directly. The plan said "ChromeVM as AggregateVM3 of
  hint+status+toast"; this is true, but our `ChromeVM` is a **facade** that
  holds an `AggregateVM3` instance, not a subclass.
- `ComponentVM[T]` is NOT a generic — `ComponentVMOf[M]` is. For most of our
  VMs the model is irrelevant; we use plain `ComponentVM` and store our own
  state on the facade.
- `Service` protocol moves to `vm/services_protocol.py` (the cleaner option
  documented in the M3 watch-outs) so `vm/` stays free of `aws_tui.services`
  imports.
- `CompositeVM` builder uses `.children(factory)` — NOT `.children_factory(...)`.
- For our `RootVM`, the message hub is created in the facade `__init__` and
  passed down via constructor injection. There is no "owned by RootVM" magic
  the way the spec phrases it — RootVM just holds a reference and exposes it.
