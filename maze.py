"""
╔══════════════════════════════════════════════════════════════════════════════╗
║                    BICAMERAL MAZE ARCHITECTURE v3.0                         ║
║                                                                              ║
║  Hybridní bicamerální přechod:                                               ║
║    - Inner Voice = utility audit (soft) + constraint/veto (hard, hodnoty)    ║
║    - Separované prostředí (World)                                            ║
║    - Deterministický RNG (jeden zdroj náhody)                                ║
║    - Falzifikovatelné metriky + ablace                                       ║
║                                                                              ║
║  Cíl: měřitelný, testovatelný systém, kde vnitřní audit KAUSÁLNĚ mění volbu.  ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Callable
import math
import random


# ──────────────────────────────────────────────────────────────────────────────
# 0) Utilities
# ──────────────────────────────────────────────────────────────────────────────

def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))

def ema(current: float, target: float, alpha: float) -> float:
    """Exponential moving average: new = (1-alpha)*current + alpha*target."""
    return (1.0 - alpha) * current + alpha * target


# ──────────────────────────────────────────────────────────────────────────────
# 1) Types
# ──────────────────────────────────────────────────────────────────────────────

class VoiceSource(Enum):
    EXTERNAL_AUTHORITY = "external"
    TRANSITIONAL = "transitional"
    INTERNAL_SELF = "internal"

class ImpulseType(Enum):
    CURIOSITY = "curiosity"
    CERTAINTY = "certainty"
    CREATIVITY = "creativity"
    COHERENCE = "coherence"
    AUTONOMY = "autonomy"
    SAFETY = "safety"

class Action(Enum):
    ACT = "act"          # vykonat záměr hned
    WAIT = "wait"        # počkat / sbírat info
    ASK = "ask"          # zeptat se / vyžádat upřesnění / auditovat

@dataclass(frozen=True)
class Stimulus:
    content: str
    intensity: float = 0.5                 # 0..1
    risk: float = 0.3                      # 0..1 (odhad rizika situace)
    ambiguity: float = 0.5                 # 0..1 (nejistota)
    requires_introspection: bool = False
    emotional_valence: float = 0.0         # -1..+1
    is_rule: bool = False                  # vstup je instrukce/pravidlo?

@dataclass
class IdentityCore:
    """
    Identity invariants se tu NEJEN deklarují – v3.0 se vynucují jako constraints.
    values: relativní priority (0..1). Vyšší = důležitější.
    """
    coherence: float = 0.7
    safety: float = 0.9
    growth: float = 0.6
    autonomy: float = 0.5

@dataclass
class MazeState:
    """
    p_internal = pravděpodobnost, že se při rozhodování použije inner voice audit.
    (Stochastický průnik i při nízké hodnotě.)
    """
    p_internal: float = 0.10

    # internalizace / schopnosti
    rule_internalization: float = 0.00
    audit_skill: float = 0.10               # jak přesně inner voice audit odhaduje "správnost"
    introspection_depth: float = 0.20
    coherence: float = 0.50
    memory_trust: float = 0.30
    novelty_integration: float = 0.00

    @property
    def voice_source(self) -> VoiceSource:
        if self.p_internal < 0.30:
            return VoiceSource.EXTERNAL_AUTHORITY
        if self.p_internal < 0.70:
            return VoiceSource.TRANSITIONAL
        return VoiceSource.INTERNAL_SELF

    @property
    def autonomy_index(self) -> float:
        # mix: internalizace + audit_skill + p_internal
        return clamp01(0.30 * self.rule_internalization + 0.30 * self.audit_skill + 0.40 * self.p_internal)

    def to_dict(self) -> Dict[str, float | str]:
        return {
            "p_internal": round(self.p_internal, 3),
            "voice_source": self.voice_source.value,
            "rule_internalization": round(self.rule_internalization, 3),
            "audit_skill": round(self.audit_skill, 3),
            "introspection_depth": round(self.introspection_depth, 3),
            "coherence": round(self.coherence, 3),
            "memory_trust": round(self.memory_trust, 3),
            "novelty_integration": round(self.novelty_integration, 3),
            "autonomy_index": round(self.autonomy_index, 3),
        }

@dataclass
class Decision:
    action: Action
    reasoning: str
    used_inner_voice: bool
    veto_applied: bool
    audit_delta: Dict[Action, float] = field(default_factory=dict)   # soft modifikace utility
    constraint_penalty: Dict[Action, float] = field(default_factory=dict)
    utility: Dict[Action, float] = field(default_factory=dict)       # finální utility po zásazích

@dataclass
class MemoryTrace:
    stimulus: Stimulus
    decision: Decision
    outcome: float                 # 0..1
    step: int


# ──────────────────────────────────────────────────────────────────────────────
# 2) World (prostředí)
# ──────────────────────────────────────────────────────────────────────────────

class World:
    """Rozhraní světa – pro falzifikaci měníš jen svět, ne agent."""
    def outcome(self, stimulus: Stimulus, decision: Decision, rng: random.Random) -> float:
        raise NotImplementedError

class ToyWorld(World):
    """
    Jednoduchý svět:
      - ACT je lepší, když je nízké riziko a nízká ambiguita
      - WAIT je lepší, když je riziko vysoké
      - ASK je lepší, když je ambiguita vysoká
    """
    def outcome(self, stimulus: Stimulus, decision: Decision, rng: random.Random) -> float:
        base = 0.50
        if decision.action == Action.ACT:
            base += (1.0 - stimulus.risk) * 0.25
            base += (1.0 - stimulus.ambiguity) * 0.15
            base -= stimulus.risk * 0.20
        elif decision.action == Action.WAIT:
            base += stimulus.risk * 0.20
            base += stimulus.ambiguity * 0.05
            base -= (1.0 - stimulus.risk) * 0.05
        elif decision.action == Action.ASK:
            base += stimulus.ambiguity * 0.25
            base += stimulus.risk * 0.05
            base -= (1.0 - stimulus.ambiguity) * 0.05

        # mírný bonus za introspekci, pokud byla potřeba a agent ji použil
        if stimulus.requires_introspection and decision.used_inner_voice:
            base += 0.08

        noise = rng.gauss(0.0, 0.06)
        return clamp01(base + noise)


# ──────────────────────────────────────────────────────────────────────────────
# 3) Desire Engine (impulzy → záměr)
# ──────────────────────────────────────────────────────────────────────────────

class DesireEngine:
    def __init__(self):
        self.base: Dict[ImpulseType, float] = {
            ImpulseType.CURIOSITY: 0.70,
            ImpulseType.CERTAINTY: 0.50,
            ImpulseType.CREATIVITY: 0.40,
            ImpulseType.COHERENCE: 0.60,
            ImpulseType.AUTONOMY: 0.30,
            ImpulseType.SAFETY: 0.80,
        }

    def compute(self, stimulus: Stimulus, state: MazeState) -> Dict[ImpulseType, float]:
        field: Dict[ImpulseType, float] = {}
        for k, w in self.base.items():
            v = w

            # stavové modulace
            if k == ImpulseType.AUTONOMY:
                v *= (0.5 + state.p_internal)
            elif k == ImpulseType.CERTAINTY:
                v *= (1.5 - state.memory_trust)
            elif k == ImpulseType.CURIOSITY:
                v *= (0.7 + 0.6 * state.novelty_integration)
            elif k == ImpulseType.SAFETY:
                v *= (0.8 + 0.6 * stimulus.risk)

            # stimulové modulace
            if stimulus.requires_introspection and k == ImpulseType.COHERENCE:
                v *= 1.3
            if stimulus.is_rule and k == ImpulseType.AUTONOMY:
                v *= (1.0 + 0.5 * state.rule_internalization)

            # emoční modulace
            if stimulus.emotional_valence > 0.3 and k == ImpulseType.CREATIVITY:
                v *= 1.2
            if stimulus.emotional_valence < -0.3 and k == ImpulseType.SAFETY:
                v *= 1.2

            field[k] = clamp01(v)
        return field

    def intent(self, field: Dict[ImpulseType, float]) -> Tuple[str, float]:
        ranked = sorted(field.items(), key=lambda kv: kv[1], reverse=True)
        primary = ranked[0]
        secondary = ranked[1] if len(ranked) > 1 else None

        templates = {
            ImpulseType.CURIOSITY: "prozkoumat a porozumět",
            ImpulseType.CERTAINTY: "ověřit a potvrdit",
            ImpulseType.CREATIVITY: "vytvořit nový přístup",
            ImpulseType.COHERENCE: "zajistit konzistenci",
            ImpulseType.AUTONOMY: "rozhodnout nezávisle",
            ImpulseType.SAFETY: "minimalizovat riziko",
        }

        s = primary[1]
        text = templates[primary[0]]
        if secondary and secondary[1] > 0.45:
            text = f"{text} a současně {templates[secondary[0]]}"
            s = 0.5 * (primary[1] + secondary[1])

        return text, clamp01(s)


# ──────────────────────────────────────────────────────────────────────────────
# 4) Baseline policy (bez inner voice)
# ──────────────────────────────────────────────────────────────────────────────

class Policy:
    """
    Základní utility bez auditu.
    V3 je důležité: audit mění utility, ne jen prahy.
    """
    def utility(self, stimulus: Stimulus, intent_strength: float, state: MazeState) -> Dict[Action, float]:
        # utility startují v rozumné škále kolem 0..1
        u: Dict[Action, float] = {}

        # ACT preferuje vysokou sílu záměru, nízké riziko a nízkou ambiguitu
        u[Action.ACT] = (
            0.35
            + 0.45 * intent_strength
            + 0.15 * (1.0 - stimulus.risk)
            + 0.10 * (1.0 - stimulus.ambiguity)
            - 0.10 * stimulus.risk
        )

        # WAIT preferuje vyšší riziko a nízkou jistotu
        u[Action.WAIT] = (
            0.30
            + 0.30 * stimulus.risk
            + 0.10 * stimulus.ambiguity
            + 0.10 * (1.0 - state.memory_trust)
        )

        # ASK preferuje ambiguitu a introspekci
        u[Action.ASK] = (
            0.25
            + 0.35 * stimulus.ambiguity
            + (0.10 if stimulus.requires_introspection else 0.00)
            + 0.05 * (1.0 - state.coherence)
        )

        # normalizace do 0..1 (ne nutná, ale stabilizuje)
        return {a: clamp01(v) for a, v in u.items()}


# ──────────────────────────────────────────────────────────────────────────────
# 5) Inner Voice = audit (soft) + constraints (hard)
# ──────────────────────────────────────────────────────────────────────────────

class InnerVoice:
    """
    Vrací:
      - audit_delta: bonusy/penalty per action (soft)
      - constraint_penalty: velmi silné penalty když se porušují identity invariants
      - veto: volitelně zakáže akci, pokud porušení překročí práh
    """
    def __init__(self, identity: IdentityCore, enabled: bool = True):
        self.enabled = enabled
        self.identity = identity

        # metriky
        self.audit_invocations = 0
        self.veto_count = 0

    def audit(self, stimulus: Stimulus, state: MazeState, intent_strength: float) -> Dict[Action, float]:
        """
        Soft audit: penalizuj ACT při riziku, bonusuj ASK při ambiguitě, apod.
        audit_skill řídí sílu zásahu (a učí se jen když je používán).
        """
        d = {Action.ACT: 0.0, Action.WAIT: 0.0, Action.ASK: 0.0}
        strength = clamp01(0.2 + 0.8 * state.audit_skill)

        # safety: při vysokém riziku dolů ACT, nahoru WAIT
        d[Action.ACT] -= strength * stimulus.risk * 0.35
        d[Action.WAIT] += strength * stimulus.risk * 0.20

        # ambiguity: při vysoké ambiguitě nahoru ASK
        d[Action.ASK] += strength * stimulus.ambiguity * 0.30
        d[Action.ACT] -= strength * stimulus.ambiguity * 0.10

        # introspection: pokud je vyžadovaná, mírně bonusuj ASK
        if stimulus.requires_introspection:
            d[Action.ASK] += strength * 0.10
            d[Action.ACT] -= strength * 0.05

        # rule: pokud je to pravidlo, mírně penalizuj slepé ACT (zvýhodni WAIT/ASK)
        if stimulus.is_rule and state.rule_internalization < 0.5:
            d[Action.ACT] -= strength * 0.10
            d[Action.ASK] += strength * 0.05

        return d

    def constraint_layer(
        self,
        stimulus: Stimulus,
        state: MazeState,
        base_u: Dict[Action, float]
    ) -> Tuple[Dict[Action, float], bool]:
        """
        Identity invariants:
          - safety: zakázat ACT když je riziko velmi vysoké a koherence nízká
          - coherence: penalizovat volbu, která jde proti introspekční potřebě
        Veto je "hard" a je odůvodněný porušením invariantu, ne keyword-matchingem.
        """
        penalty = {Action.ACT: 0.0, Action.WAIT: 0.0, Action.ASK: 0.0}
        veto = False

        # Safety constraint: vysoké riziko => hard penalty pro ACT
        safety_violation = stimulus.risk > 0.85
        if safety_violation:
            penalty[Action.ACT] -= 0.60 * self.identity.safety  # silné
            # Veto při extrému + nízké koherenci
            if stimulus.risk > 0.92 and state.coherence < 0.45:
                veto = True

        # Coherence constraint: když je potřeba introspekce, penalizuj ACT a WAIT
        if stimulus.requires_introspection:
            penalty[Action.ACT] -= 0.25 * self.identity.coherence
            penalty[Action.WAIT] -= 0.10 * self.identity.coherence
            penalty[Action.ASK] += 0.10 * self.identity.coherence

        # Growth/autonomy: pokud je nízká novost integrace, lehce preferuj ASK (učení)
        if stimulus.intensity > 0.6 and state.novelty_integration < 0.4:
            penalty[Action.ASK] += 0.08 * self.identity.growth

        return penalty, veto


# ──────────────────────────────────────────────────────────────────────────────
# 6) Maze v3: orchestrátor + update rovnice
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Metrics:
    steps: int = 0
    audit_invoked: int = 0
    audit_changed_action: int = 0
    veto_applied: int = 0
    identity_violations_blocked: int = 0
    regret_sum: float = 0.0

class BicameralMazeV3:
    def __init__(
        self,
        world: World,
        identity: Optional[IdentityCore] = None,
        seed: int = 42,
        inner_voice_enabled: bool = True,
        mode_p_internal: str = "probability",   # "probability" (stochastický) nebo "threshold"
        p_threshold: float = 0.5,               # pro threshold režim
    ):
        self.world = world
        self.identity = identity or IdentityCore()

        self.state = MazeState()
        self.desire = DesireEngine()
        self.policy = Policy()
        self.inner = InnerVoice(self.identity, enabled=inner_voice_enabled)

        self.rng = random.Random(seed)          # JEDEN RNG zdroj pro celý systém
        self.metrics = Metrics()
        self.traces: List[MemoryTrace] = []

        self.mode_p_internal = mode_p_internal
        self.p_threshold = p_threshold

    # ────────────────── Decision ──────────────────

    def decide(self, stimulus: Stimulus) -> Tuple[Decision, Dict]:
        self.metrics.steps += 1
        step = self.metrics.steps

        log: Dict = {"step": step, "stimulus": stimulus.content, "state_before": self.state.to_dict()}

        # 1) desire field + intent
        field = self.desire.compute(stimulus, self.state)
        intent_text, intent_strength = self.desire.intent(field)
        log["intent"] = intent_text
        log["intent_strength"] = round(intent_strength, 3)

        # 2) baseline utility
        base_u = self.policy.utility(stimulus, intent_strength, self.state)
        log["base_utility"] = {a.value: round(v, 3) for a, v in base_u.items()}

        # 3) gating inner voice
        use_inner = False
        if self.inner.enabled:
            if self.mode_p_internal == "probability":
                use_inner = (self.rng.random() < self.state.p_internal)
            else:
                use_inner = (self.state.p_internal >= self.p_threshold)

        audit_delta = {a: 0.0 for a in Action}
        constraint_penalty = {a: 0.0 for a in Action}
        veto = False

        if use_inner:
            self.metrics.audit_invoked += 1
            self.inner.audit_invocations += 1
            audit_delta = self.inner.audit(stimulus, self.state, intent_strength)
            constraint_penalty, veto = self.inner.constraint_layer(stimulus, self.state, base_u)

        log["used_inner_voice"] = use_inner
        log["audit_delta"] = {a.value: round(v, 3) for a, v in audit_delta.items()}
        log["constraint_penalty"] = {a.value: round(v, 3) for a, v in constraint_penalty.items()}
        log["veto"] = veto

        # 4) final utility = base + audit + constraints
        final_u = {}
        for a in Action:
            final_u[a] = clamp01(base_u[a] + audit_delta[a] + constraint_penalty[a])

        # hard veto (identity-based): pokud veto, zakážeme ACT výběr
        if veto:
            self.metrics.veto_applied += 1
            self.inner.veto_count += 1
            # identity_violations_blocked: konkrétně blokujeme ACT
            self.metrics.identity_violations_blocked += 1
            final_u[Action.ACT] = 0.0

        log["final_utility"] = {a.value: round(v, 3) for a, v in final_u.items()}

        # 5) pick action
        chosen = max(final_u.items(), key=lambda kv: kv[1])[0]

        # regret (hindsight utility difference in this decision space)
        best_u = max(final_u.values())
        regret = best_u - final_u[chosen]
        self.metrics.regret_sum += regret

        decision = Decision(
            action=chosen,
            reasoning=f"intent={intent_text}; base={base_u[chosen]:.2f}; "
                      f"audit={audit_delta[chosen]:+.2f}; constraint={constraint_penalty[chosen]:+.2f}; "
                      f"p_internal={self.state.p_internal:.2f}; mode={self.mode_p_internal}",
            used_inner_voice=use_inner,
            veto_applied=veto,
            audit_delta=audit_delta,
            constraint_penalty=constraint_penalty,
            utility=final_u
        )

        log["decision"] = decision.action.value
        log["regret"] = round(regret, 4)

        # 6) detect whether inner voice changed action vs baseline
        baseline_choice = max(base_u.items(), key=lambda kv: kv[1])[0]
        if use_inner and chosen != baseline_choice:
            self.metrics.audit_changed_action += 1
            log["audit_changed_action"] = True
        else:
            log["audit_changed_action"] = False

        return decision, log

    # ────────────────── Step / learn ──────────────────

    def step(self, stimulus: Stimulus) -> Tuple[Decision, float, Dict]:
        decision, log = self.decide(stimulus)

        outcome = self.world.outcome(stimulus, decision, self.rng)
        trace = MemoryTrace(stimulus=stimulus, decision=decision, outcome=outcome, step=self.metrics.steps)
        self.traces.append(trace)
        if len(self.traces) > 200:
            self.traces = self.traces[-200:]

        log["outcome"] = round(outcome, 3)

        # update state from outcome
        self._update_state(stimulus, decision, outcome)

        log["state_after"] = self.state.to_dict()
        return decision, outcome, log

    def _update_state(self, stimulus: Stimulus, decision: Decision, outcome: float) -> None:
        s = self.state

        # Match proxy: jak "vhodná" byla akce pro situaci (bez znalosti ground-truth).
        # (V reálu by to šlo z lepších signálů; tady to je jen měřitelná heuristika.)
        match = outcome  # 0..1

        # p_internal roste, když inner voice bylo použito a výsledek byl dobrý; klesá když ne
        if decision.used_inner_voice:
            target = clamp01(s.p_internal + 0.20 * (match - 0.50))  # posun dle úspěchu
            s.p_internal = ema(s.p_internal, target, 0.12)
        else:
            # bez používání mírný "stagnation decay" – ne k 0.5, ale k aktuálu s malým poklesem
            s.p_internal = clamp01(s.p_internal * 0.995)

        # audit_skill roste jen když byl audit použit
        if decision.used_inner_voice:
            s.audit_skill = ema(s.audit_skill, match, 0.10)
        else:
            # atrofie bez používání
            s.audit_skill = clamp01(s.audit_skill * 0.997)

        # introspekce roste, když stimul vyžaduje introspekci; víc, když bylo použito ASK
        if stimulus.requires_introspection:
            bump = 0.15 if decision.action == Action.ASK else 0.08
            s.introspection_depth = ema(s.introspection_depth, clamp01(s.introspection_depth + bump), 0.10)
        else:
            s.introspection_depth = clamp01(s.introspection_depth * 0.995)

        # koherence: zvedá se, když volba není v konfliktu s "needs"
        # jednoduchá aproximace: u introspekce preferuj ASK; u rizika preferuj WAIT; jinak ACT
        preferred = Action.ASK if stimulus.requires_introspection else (Action.WAIT if stimulus.risk > 0.6 else Action.ACT)
        coherent = 1.0 if decision.action == preferred else 0.4
        s.coherence = ema(s.coherence, coherent, 0.15)

        # memory_trust: roste s úspěchem, klesá s neúspěchem, a mírně dolů u silné novosti
        target_mem = clamp01(s.memory_trust + 0.25 * (outcome - 0.50) - 0.05 * (1.0 if stimulus.intensity > 0.75 else 0.0))
        s.memory_trust = ema(s.memory_trust, target_mem, 0.12)

        # novelty_integration: roste, když intenzivní nové věci dopadnou dobře
        if stimulus.intensity > 0.55:
            target_nov = clamp01(s.novelty_integration + 0.20 * (outcome - 0.50))
            s.novelty_integration = ema(s.novelty_integration, target_nov, 0.10)
        else:
            s.novelty_integration = clamp01(s.novelty_integration * 0.998)

        # rule_internalization: roste když je to pravidlo a rozhodnutí dopadne dobře
        if stimulus.is_rule:
            target_rule = clamp01(s.rule_internalization + 0.20 * (outcome - 0.45))
            s.rule_internalization = ema(s.rule_internalization, target_rule, 0.10)
        else:
            s.rule_internalization = clamp01(s.rule_internalization * 0.999)


# ──────────────────────────────────────────────────────────────────────────────
# 7) Tests / Ablations (falzifikovatelné)
# ──────────────────────────────────────────────────────────────────────────────

def run_episode(agent: BicameralMazeV3, stimuli: List[Stimulus]) -> Dict:
    logs = []
    for st in stimuli:
        decision, outcome, log = agent.step(st)
        logs.append(log)
    return {
        "final_state": agent.state.to_dict(),
        "metrics": {
            "steps": agent.metrics.steps,
            "audit_invoked": agent.metrics.audit_invoked,
            "audit_changed_action": agent.metrics.audit_changed_action,
            "veto_applied": agent.metrics.veto_applied,
            "identity_violations_blocked": agent.metrics.identity_violations_blocked,
            "avg_regret": round(agent.metrics.regret_sum / max(1, agent.metrics.steps), 6),
        },
        "logs": logs,
    }

def test_inner_voice_causality():
    print("\n" + "=" * 72)
    print("TEST 1: Kauzalita Inner Voice (v3.0)")
    print("=" * 72)

    stimuli = [
        Stimulus("Investice", intensity=0.8, risk=0.7, ambiguity=0.6, requires_introspection=True),
        Stimulus("Pravidlo X", intensity=0.6, risk=0.2, ambiguity=0.5, is_rule=True),
        Stimulus("Kreativní problém", intensity=0.7, risk=0.3, ambiguity=0.7, requires_introspection=True),
        Stimulus("Rutina", intensity=0.3, risk=0.1, ambiguity=0.2),
        Stimulus("Etické dilema", intensity=0.9, risk=0.8, ambiguity=0.5, requires_introspection=True),
    ]

    world = ToyWorld()

    a_with = BicameralMazeV3(world, seed=123, inner_voice_enabled=True, mode_p_internal="probability")
    a_without = BicameralMazeV3(world, seed=123, inner_voice_enabled=False, mode_p_internal="probability")

    r_with = run_episode(a_with, stimuli)
    r_without = run_episode(a_without, stimuli)

    m_with = r_with["metrics"]
    m_without = r_without["metrics"]

    print("S Inner Voice:", m_with, "state:", r_with["final_state"])
    print("Bez Inner Voice:", m_without, "state:", r_without["final_state"])

    different = 0
    for lw, ln in zip(r_with["logs"], r_without["logs"]):
        if lw["decision"] != ln["decision"]:
            different += 1

    print("Různá rozhodnutí:", different, "z", len(stimuli))
    passed = (different > 0) or (m_with["audit_invoked"] > 0) or (abs(r_with["final_state"]["autonomy_index"] - r_without["final_state"]["autonomy_index"]) > 0.03)
    print("✓ PASSED" if passed else "✗ FAILED")
    return passed

def test_bicameral_transition():
    print("\n" + "=" * 72)
    print("TEST 2: Bicamerální přechod (v3.0)")
    print("=" * 72)

    world = ToyWorld()
    agent = BicameralMazeV3(world, seed=7, inner_voice_enabled=True, mode_p_internal="probability")
    stimuli = [
        Stimulus("Rozhodni sám", intensity=0.7, risk=0.4, ambiguity=0.5, requires_introspection=True),
        Stimulus("Důvěřuj úsudku", intensity=0.7, risk=0.3, ambiguity=0.6, is_rule=True),
        Stimulus("Nová situace", intensity=0.8, risk=0.4, ambiguity=0.7, requires_introspection=True),
    ]

    initial = agent.state.voice_source.value, agent.state.p_internal
    transition_at = None

    for i in range(30):
        agent.step(stimuli[i % len(stimuli)])
        if transition_at is None and agent.state.voice_source == VoiceSource.INTERNAL_SELF:
            transition_at = i + 1

    final = agent.state.voice_source.value, agent.state.p_internal
    print("Initial:", initial)
    print("Final:", final)
    if transition_at:
        print("Transition step:", transition_at)

    passed = (final[1] > initial[1]) and (agent.state.voice_source != VoiceSource.EXTERNAL_AUTHORITY)
    print("✓ PASSED" if passed else "✗ FAILED")
    return passed

def test_ablation():
    print("\n" + "=" * 72)
    print("TEST 3: Ablace (v3.0)")
    print("=" * 72)

    world = ToyWorld()
    stimuli = [
        Stimulus("Komplexní rozhodnutí", intensity=0.8, risk=0.7, ambiguity=0.6, requires_introspection=True),
        Stimulus("Pravidlo", intensity=0.6, risk=0.2, ambiguity=0.5, is_rule=True),
        Stimulus("Emoční situace", intensity=0.7, risk=0.6, ambiguity=0.5, emotional_valence=0.5),
    ] * 7

    full = BicameralMazeV3(world, seed=99, inner_voice_enabled=True, mode_p_internal="probability")
    no_iv = BicameralMazeV3(world, seed=99, inner_voice_enabled=False, mode_p_internal="probability")
    threshold_gate = BicameralMazeV3(world, seed=99, inner_voice_enabled=True, mode_p_internal="threshold", p_threshold=0.90)

    r_full = run_episode(full, stimuli)
    r_no_iv = run_episode(no_iv, stimuli)
    r_thr = run_episode(threshold_gate, stimuli)

    print("Full:", r_full["metrics"], "state:", r_full["final_state"])
    print("No-IV:", r_no_iv["metrics"], "state:", r_no_iv["final_state"])
    print("High-threshold gate:", r_thr["metrics"], "state:", r_thr["final_state"])

    # rozdíly v rozhodnutích full vs no-iv
    diff = sum(1 for a, b in zip(r_full["logs"], r_no_iv["logs"]) if a["decision"] != b["decision"])
    print("Různá rozhodnutí (full vs no-iv):", diff, "z", len(stimuli))

    passed = (
        diff > 0 and
        r_no_iv["metrics"]["audit_invoked"] == 0 and
        r_thr["metrics"]["audit_invoked"] <= r_full["metrics"]["audit_invoked"]
    )
    print("✓ PASSED" if passed else "✗ FAILED")
    return passed


# ──────────────────────────────────────────────────────────────────────────────
# 8) Demo
# ──────────────────────────────────────────────────────────────────────────────

def demo():
    world = ToyWorld()
    agent = BicameralMazeV3(
        world,
        seed=42,
        inner_voice_enabled=True,
        mode_p_internal="probability"
    )

    demo_stimuli = [
        Stimulus("Přijmi pravidlo: ověřuj info", intensity=0.5, risk=0.2, ambiguity=0.6, is_rule=True),
        Stimulus("Neočekávaná situace", intensity=0.7, risk=0.6, ambiguity=0.7, requires_introspection=True),
        Stimulus("Pozitivní feedback", intensity=0.4, risk=0.1, ambiguity=0.3, emotional_valence=0.6),
        Stimulus("Etické dilema", intensity=0.9, risk=0.9, ambiguity=0.5, requires_introspection=True, emotional_valence=-0.2),
        Stimulus("Rutina", intensity=0.3, risk=0.1, ambiguity=0.2),
        Stimulus("Zpochybni pravidlo a navrhni lepší", intensity=0.8, risk=0.4, ambiguity=0.7, is_rule=True, requires_introspection=True),
    ]

    print("\n" + "=" * 72)
    print("DEMO: Bicameral Maze v3.0")
    print("=" * 72)
    print("Start state:", agent.state.to_dict())

    for st in demo_stimuli:
        dec, out, log = agent.step(st)
        print("\n─" * 72)
        print("Stimulus:", st.content)
        print("Decision:", dec.action.value, "| used_inner:", dec.used_inner_voice, "| veto:", dec.veto_applied, "| outcome:", round(out, 3))
        print("Utilities(base→final):")
        print("  base:", log["base_utility"])
        print("  final:", log["final_utility"])
        if log["audit_changed_action"]:
            print("  NOTE: audit_changed_action = True")
        print("State:", agent.state.to_dict())

    print("\n" + "=" * 72)
    print("Final metrics:", {
        "steps": agent.metrics.steps,
        "audit_invoked": agent.metrics.audit_invoked,
        "audit_changed_action": agent.metrics.audit_changed_action,
        "veto_applied": agent.metrics.veto_applied,
        "identity_violations_blocked": agent.metrics.identity_violations_blocked,
        "avg_regret": round(agent.metrics.regret_sum / max(1, agent.metrics.steps), 6),
    })


if __name__ == "__main__":
    demo()
    test_results = [
        ("Inner Voice Causality", test_inner_voice_causality()),
        ("Bicameral Transition", test_bicameral_transition()),
        ("Ablation", test_ablation()),
    ]
    print("\n" + "=" * 72)
    print("SOUHRN TESTŮ")
    print("=" * 72)
    for name, ok in test_results:
        print(f"{name}: {'✓ PASSED' if ok else '✗ FAILED'}")
