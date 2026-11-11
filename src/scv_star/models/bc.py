"""Behaviour-cloning policy for the frozen TvT BC cache."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
from torch import nn
from torch.nn import functional as F

from scv_star.data.bc_features import (
    ENTITY_BOOLEAN_INDEX,
    ENTITY_CATEGORICAL_INDEX,
    ENTITY_NUMERIC_INDEX,
    NUMERIC_FIELDS,
    SCALAR_INPUT_FIELDS,
    TARGET_KINDS,
)

MODEL_SCHEMA = "tvt-bc-model-v1"
HEAD_NAMES = (
    "ability",
    "kind",
    "selection",
    "target_kind",
    "target_unit",
    "target_point",
    "queue",
    "delay",
)
DEFAULT_HEAD_WEIGHTS = {
    "ability": 1.0,
    "kind": 0.5,
    "selection": 1.0,
    "target_kind": 0.25,
    "target_unit": 0.5,
    "target_point": 0.5,
    "queue": 0.25,
    "delay": 0.25,
}


@dataclass(frozen=True)
class BCModelConfig:
    entity_width: int = 128
    entity_layers: int = 2
    entity_heads: int = 4
    entity_ff: int = 512
    dropout: float = 0.1
    scalar_width: int = 128
    lstm_hidden: int = 256
    lstm_layers: int = 2
    selection_hidden: int = 128
    max_selection_steps: int = 128
    ability_head_hidden: int = 0
    selection_uses_pooled: bool = False


class BCModel(nn.Module):
    """Entity/scalar encoder, two-layer LSTM and masked autoregressive action heads."""

    def __init__(
        self,
        *,
        config: BCModelConfig,
        ability_count: int,
        unit_type_vocab,
        entity_mean,
        entity_std,
        scalar_mean,
        scalar_std,
    ):
        super().__init__()
        if ability_count < 1 or len(entity_mean) != len(NUMERIC_FIELDS):
            raise ValueError("Invalid model vocabulary or normalization")
        if len(entity_std) != len(NUMERIC_FIELDS) or len(scalar_mean) != len(SCALAR_INPUT_FIELDS):
            raise ValueError("Invalid normalization dimensions")
        self.config = config
        self.ability_count = int(ability_count)
        self.entity_width = config.entity_width
        self.scalar_width = config.scalar_width
        self.lstm_hidden = config.lstm_hidden
        self.selection_hidden = config.selection_hidden
        self.register_buffer("entity_mean", torch.tensor(entity_mean, dtype=torch.float32))
        self.register_buffer("entity_std", torch.tensor(entity_std, dtype=torch.float32))
        self.register_buffer("scalar_mean", torch.tensor(scalar_mean, dtype=torch.float32))
        self.register_buffer("scalar_std", torch.tensor(scalar_std, dtype=torch.float32))
        self.register_buffer("numeric_index", torch.tensor(ENTITY_NUMERIC_INDEX, dtype=torch.long))
        self.register_buffer("boolean_index", torch.tensor(ENTITY_BOOLEAN_INDEX, dtype=torch.long))
        self._register_unit_types(unit_type_vocab)

        width = config.entity_width
        self.unit_type_embed = nn.Embedding(len(unit_type_vocab) + 1, width)
        self.alliance_embed = nn.Embedding(5, width)
        self.display_embed = nn.Embedding(5, width)
        self.stale_embed = nn.Embedding(2, width)
        self.numeric = nn.Linear(len(NUMERIC_FIELDS) + len(ENTITY_BOOLEAN_INDEX), width)
        self.entity_norm = nn.LayerNorm(width)
        layer = nn.TransformerEncoderLayer(
            d_model=width,
            nhead=config.entity_heads,
            dim_feedforward=config.entity_ff,
            dropout=config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.transformer = nn.TransformerEncoder(
            layer, num_layers=config.entity_layers, enable_nested_tensor=False
        )
        self.scalar_encoder = nn.Sequential(
            nn.Linear(len(SCALAR_INPUT_FIELDS), config.scalar_width),
            nn.ReLU(),
            nn.Linear(config.scalar_width, config.scalar_width),
            nn.ReLU(),
        )
        self.lstm = nn.LSTM(
            width + config.scalar_width,
            config.lstm_hidden,
            num_layers=config.lstm_layers,
            batch_first=True,
            dropout=config.dropout if config.lstm_layers > 1 else 0.0,
        )
        self.ability_embed = nn.Embedding(self.ability_count, width)
        head_width = config.lstm_hidden + (width if config.ability_head_hidden else 0)
        if config.ability_head_hidden:
            self.ability_head = nn.Sequential(
                nn.Linear(head_width, config.ability_head_hidden),
                nn.GELU(),
                nn.Linear(config.ability_head_hidden, self.ability_count),
            )
        else:
            self.ability_head = nn.Linear(head_width, self.ability_count)
        self.kind_embed = nn.Embedding(2, width)
        self.kind_head = nn.Linear(config.lstm_hidden, 2)
        self.selection_context = self.entity_width if config.selection_uses_pooled else 0
        self.selection_init = nn.Linear(
            config.lstm_hidden + 2 * width + self.selection_context, config.selection_hidden
        )
        self.selection_gru = nn.GRUCell(3 * width, config.selection_hidden)
        self.selection_start = nn.Parameter(torch.zeros(width))
        self.selection_stop = nn.Parameter(torch.zeros(config.selection_hidden))
        self.target_kind_head = nn.Linear(config.lstm_hidden + 3 * width, len(TARGET_KINDS))
        self.target_kind_embed = nn.Embedding(len(TARGET_KINDS), width)
        condition_width = config.lstm_hidden + 4 * width
        self.target_unit_query = nn.Linear(condition_width, width)
        self.target_point_head = nn.Sequential(
            nn.Linear(condition_width, 128), nn.ReLU(), nn.Linear(128, 2)
        )
        self.queue_head = nn.Sequential(
            nn.Linear(condition_width, 128), nn.ReLU(), nn.Linear(128, 2)
        )
        self.delay_head = nn.Sequential(
            nn.Linear(condition_width, 128), nn.ReLU(), nn.Linear(128, 1)
        )
        self.dropout = nn.Dropout(config.dropout)

    def _register_unit_types(self, unit_type_vocab):
        vocab = sorted(int(value) for value in unit_type_vocab)
        if len(vocab) != len(set(vocab)):
            raise ValueError("Duplicate unit types in the vocabulary")
        size = max(vocab) + 1 if vocab else 1
        mapping = torch.zeros(max(size, 1), dtype=torch.long)
        for index, raw in enumerate(vocab, start=1):
            if raw < 0 or raw >= mapping.numel():
                raise ValueError("Unit type vocabulary contains a negative or oversized ID")
            mapping[raw] = index
        self.register_buffer("unit_type_map", mapping)

    def _unit_type_indices(self, values):
        raw = values.long()
        inside = (raw >= 0) & (raw < self.unit_type_map.numel())
        safe = torch.where(inside, raw, torch.zeros_like(raw))
        mapped = self.unit_type_map[safe]
        return torch.where(inside, mapped, torch.zeros_like(mapped))

    def encode(self, entities, entity_mask, stale, scalars):
        batch, steps, count, _ = entities.shape
        flat = entities.reshape(batch * steps, count, -1)
        mask = entity_mask.reshape(batch * steps, count)
        stale_mask = stale.reshape(batch * steps, count)
        categorical = (
            self.unit_type_embed(self._unit_type_indices(flat[..., ENTITY_CATEGORICAL_INDEX[0]]))
            + self.alliance_embed(flat[..., ENTITY_CATEGORICAL_INDEX[1]].long().clamp(0, 4))
            + self.display_embed(flat[..., ENTITY_CATEGORICAL_INDEX[2]].long().clamp(0, 4))
            + self.stale_embed(stale_mask.long())
        )
        numeric = flat.index_select(-1, self.numeric_index)
        numeric = (numeric - self.entity_mean) / self.entity_std
        booleans = flat.index_select(-1, self.boolean_index)
        features = torch.cat((numeric, booleans), dim=-1)
        encoded = self.entity_norm(self.numeric(features) + categorical)
        encoded = self.transformer(encoded, src_key_padding_mask=~mask)
        encoded = torch.nan_to_num(encoded, nan=0.0, posinf=0.0, neginf=0.0)
        encoded = encoded * mask.unsqueeze(-1)
        pooled = encoded.sum(1) / mask.sum(1, keepdim=True).clamp(min=1)
        scalar_input = (scalars.reshape(batch * steps, -1) - self.scalar_mean) / self.scalar_std
        return (
            encoded.reshape(batch, steps, count, self.entity_width),
            pooled.reshape(batch, steps, self.entity_width),
            scalar_input.reshape(batch, steps, -1),
        )

    def recurrent(self, entities, entity_mask, stale, scalars):
        entity_emb, pooled, scalar_input = self.encode(entities, entity_mask, stale, scalars)
        state = self.lstm(torch.cat((pooled, self.scalar_encoder(scalar_input)), dim=-1))[0]
        return entity_emb, state, pooled

    def _ability_input(self, state, pooled):
        if self.config.ability_head_hidden:
            # Keep the per-step encoder representation next to the recurrent
            # summary: the LSTM compresses it and a linear head cannot recover it.
            return torch.cat((state, pooled), dim=-1)
        return state

    def _selected_summary(self, entity_emb, indices, selected_mask):
        batch, steps, count, width = entity_emb.shape
        flat = entity_emb.reshape(batch * steps, count, width)
        index = indices.reshape(batch * steps, -1).clamp(min=0)
        gathered = torch.gather(flat, 1, index.unsqueeze(-1).expand(-1, -1, width))
        weights = selected_mask.reshape(batch * steps, -1).float().unsqueeze(-1)
        return (gathered * weights).sum(1) / weights.sum(1).clamp(min=1.0)

    def _selection_init_input(self, flat_state, ability_emb, kind_emb, pooled):
        """Decoder init input; the pooled skip is appended only when enabled.

        forward() and act() both call this: they diverged once when the option
        was added to the training path only, so the concat lives in one place.
        """
        parts = [flat_state, ability_emb, kind_emb]
        if self.selection_context:
            parts.append(pooled.reshape(flat_state.shape[0], self.entity_width))
        return torch.cat(parts, dim=-1)

    def _selection_logits(
        self,
        entity_emb,
        state,
        ability_emb,
        kind_emb,
        indices,
        selected_mask,
        entity_mask,
        pooled=None,
    ):
        batch, steps, count, width = entity_emb.shape
        flat = entity_emb.reshape(batch * steps, count, width)
        flat_mask = entity_mask.reshape(batch * steps, count)
        flat_state = state.reshape(batch * steps, -1)
        flat_ability = ability_emb.reshape(batch * steps, -1)
        flat_kind = kind_emb.reshape(batch * steps, -1)
        hidden = torch.tanh(
            self.selection_init(
                self._selection_init_input(flat_state, flat_ability, flat_kind, pooled)
            )
        )
        previous = self.selection_start.expand(batch * steps, -1)
        selection_mask = selected_mask.reshape(batch * steps, -1)
        index = indices.reshape(batch * steps, -1)
        maximum = index.shape[-1]
        logits = []
        for step in range(maximum + 1):
            hidden = self.selection_gru(
                torch.cat((previous, flat_ability, flat_kind), dim=-1), hidden
            )
            scores = torch.einsum("nw,new->ne", hidden, flat)
            # Padded entity slots must not consume probability mass: without this
            # the selection loss would depend on the batch's maximum entity count,
            # and training would disagree with act(), which already masks them.
            scores = scores.masked_fill(~flat_mask, torch.finfo(scores.dtype).min)
            stop = torch.einsum("nw,w->n", hidden, self.selection_stop)
            logits.append(torch.cat((scores, stop.unsqueeze(1)), dim=1))
            if step < maximum:
                chosen = index[:, step].clamp(min=0)
                chosen_emb = torch.gather(
                    flat, 1, chosen.view(-1, 1, 1).expand(-1, 1, width)
                ).squeeze(1)
                valid = selection_mask[:, step]
                previous = torch.where(
                    valid.unsqueeze(-1),
                    chosen_emb,
                    self.selection_start.expand(batch * steps, width),
                )
        return logits

    def _pointer(self, query, entities, entity_mask):
        scores = torch.einsum("nw,new->ne", query, entities)
        return scores.masked_fill(~entity_mask, torch.finfo(scores.dtype).min)

    def forward(self, batch):
        entities = batch["entities"]
        entity_mask = batch["entity_mask"]
        stale = batch["stale"]
        scalars = batch["scalars"]
        targets = batch["targets"]
        entity_emb, state, pooled = self.recurrent(entities, entity_mask, stale, scalars)
        batch_size, steps = targets["ability"].shape
        ability_logits = self.ability_head(self._ability_input(state, pooled))
        kind_logits = self.kind_head(state)
        ability_emb = self.ability_embed(targets["ability"])
        kind_emb = self.kind_embed(targets["kind"])
        indices = targets["selected_indices"]
        selected_mask = targets["selected_mask"]
        summary = self._selected_summary(entity_emb, indices, selected_mask)
        selection_logits = self._selection_logits(
            entity_emb, state, ability_emb, kind_emb, indices, selected_mask, entity_mask, pooled
        )
        flat_state = state.reshape(batch_size * steps, -1)
        flat_ability = ability_emb.reshape(batch_size * steps, -1)
        flat_kind = kind_emb.reshape(batch_size * steps, -1)
        target_kind_logits = self.target_kind_head(
            torch.cat((flat_state, flat_ability, summary, flat_kind), dim=-1)
        )
        target_kind = targets["target_kind"].reshape(-1)
        target_kind_emb = self.target_kind_embed(target_kind)
        condition = torch.cat(
            (flat_state, flat_ability, summary, flat_kind, target_kind_emb), dim=-1
        )
        flat_entities = entity_emb.reshape(
            batch_size * steps, entity_emb.shape[2], self.entity_width
        )
        flat_mask = entity_mask.reshape(batch_size * steps, -1)
        target_unit_logits = self._pointer(
            self.target_unit_query(condition), flat_entities, flat_mask
        )
        return {
            "ability": ability_logits,
            "kind": kind_logits,
            "selection": selection_logits,
            "target_kind": target_kind_logits,
            "target_unit": target_unit_logits,
            "target_point": torch.sigmoid(self.target_point_head(condition)),
            "queue": self.queue_head(condition),
            "delay": self.delay_head(condition).squeeze(-1),
        }

    @torch.no_grad()
    def act(self, batch):
        """Sample a full action from legal observations; not connected to SC2 here."""
        self.eval()
        entities = batch["entities"]
        entity_mask = batch["entity_mask"]
        stale = batch["stale"]
        scalars = batch["scalars"]
        entity_emb, state, pooled = self.recurrent(entities, entity_mask, stale, scalars)
        batch_size, steps, count = entity_mask.shape
        rows = batch_size * steps
        flat_state = state.reshape(rows, -1)
        flat_entities = entity_emb.reshape(rows, count, self.entity_width)
        flat_mask = entity_mask.reshape(rows, count)
        ability_logits = self.ability_head(self._ability_input(state, pooled)).reshape(rows, -1)
        ability = torch.distributions.Categorical(logits=ability_logits).sample()
        ability_emb = self.ability_embed(ability)
        kind_logits = self.kind_head(state).reshape(rows, -1)
        kind = torch.distributions.Categorical(logits=kind_logits).sample()
        kind_emb = self.kind_embed(kind)
        hidden = torch.tanh(
            self.selection_init(
                self._selection_init_input(flat_state, ability_emb, kind_emb, pooled)
            )
        )
        previous = self.selection_start.expand(rows, -1)
        summary = torch.zeros(rows, self.entity_width, device=ability.device)
        counts = torch.zeros(rows, device=ability.device)
        selected = torch.zeros(
            rows,
            self.config.max_selection_steps,
            dtype=torch.long,
            device=ability.device,
        )
        selected_mask = torch.zeros_like(selected, dtype=torch.bool)
        blocked = torch.zeros(rows, count, dtype=torch.bool, device=ability.device)
        active = torch.ones(rows, dtype=torch.bool, device=ability.device)
        for step in range(self.config.max_selection_steps):
            hidden = self.selection_gru(
                torch.cat((previous, ability_emb, kind_emb), dim=-1), hidden
            )
            scores = torch.einsum("nw,new->ne", hidden, flat_entities)
            stop = torch.einsum("nw,w->n", hidden, self.selection_stop)
            logits = torch.cat((scores, stop.unsqueeze(1)), dim=1)
            invalid = torch.cat((blocked | ~flat_mask, torch.zeros_like(active)[:, None]), dim=1)
            logits = logits.masked_fill(invalid, torch.finfo(logits.dtype).min)
            choice = torch.distributions.Categorical(logits=logits).sample()
            is_stop = choice == count
            taking = active & ~is_stop
            if taking.any():
                chosen = choice.clamp(max=count - 1)
                selected[:, step] = torch.where(taking, chosen, selected[:, step])
                selected_mask[:, step] = taking
                row_index = torch.arange(rows, device=blocked.device)[taking]
                blocked[row_index, chosen[taking]] = True
                chosen_emb = torch.gather(
                    flat_entities,
                    1,
                    chosen.view(-1, 1, 1).expand(-1, 1, self.entity_width),
                ).squeeze(1)
                summary = summary + chosen_emb * taking.float().unsqueeze(1)
                counts = counts + taking.float()
                previous = torch.where(
                    taking.unsqueeze(1),
                    chosen_emb,
                    self.selection_start.expand(rows, self.entity_width),
                )
            active = active & ~is_stop
            if not active.any():
                break
        summary = summary / counts.clamp(min=1).unsqueeze(1)
        target_kind_logits = self.target_kind_head(
            torch.cat((flat_state, ability_emb, summary, kind_emb), dim=-1)
        )
        target_kind = torch.distributions.Categorical(logits=target_kind_logits).sample()
        autocast = kind == 1
        target_kind = torch.where(autocast, torch.zeros_like(target_kind), target_kind)
        target_kind_emb = self.target_kind_embed(target_kind)
        condition = torch.cat((flat_state, ability_emb, summary, kind_emb, target_kind_emb), dim=-1)
        unit_logits = self._pointer(self.target_unit_query(condition), flat_entities, flat_mask)
        target_unit = torch.distributions.Categorical(logits=unit_logits).sample()
        target_point = torch.sigmoid(self.target_point_head(condition))
        queue = torch.distributions.Categorical(logits=self.queue_head(condition)).sample()
        queue = torch.where(autocast, torch.zeros_like(queue), queue)
        delay = torch.expm1(self.delay_head(condition).squeeze(-1)).clamp(min=0)
        return {
            "ability": ability.reshape(batch_size, steps),
            "kind": kind.reshape(batch_size, steps),
            "selected_indices": selected.reshape(batch_size, steps, -1),
            "selected_mask": selected_mask.reshape(batch_size, steps, -1),
            "target_kind": target_kind.reshape(batch_size, steps),
            "target_unit": target_unit.reshape(batch_size, steps),
            "target_point": target_point.reshape(batch_size, steps, 2),
            "queue": queue.reshape(batch_size, steps),
            "delay": delay.reshape(batch_size, steps),
        }


def build_bc_model(cache, model_config):
    config = BCModelConfig(**model_config)
    normalization = cache["normalization"]
    return BCModel(
        config=config,
        ability_count=len(cache["catalog"]["ability_ids"]),
        unit_type_vocab=cache["unit_type_vocab"],
        entity_mean=normalization["entity_mean"],
        entity_std=normalization["entity_std"],
        scalar_mean=normalization["scalar_mean"],
        scalar_std=normalization["scalar_std"],
    )


def categorical_loss(logits, target, active):
    losses = F.cross_entropy(logits, target, reduction="none")
    return (losses * active).sum(), active.sum()


def bc_loss(outputs, targets, weights=None):
    """Masked multi-head loss; inactive heads contribute a zero value and zero count."""
    weights = DEFAULT_HEAD_WEIGHTS if weights is None else dict(weights)
    ability_logits = outputs["ability"]
    batch, steps = ability_logits.shape[:2]
    rows = batch * steps
    step_active = targets["step_mask"].reshape(-1)
    ability_target = targets["ability"].reshape(-1)
    ability, ability_count = categorical_loss(
        ability_logits.reshape(rows, -1),
        ability_target,
        step_active,
    )
    kind_target = targets["kind"].reshape(-1)
    kind_active = step_active
    kind_loss, kind_count = categorical_loss(
        outputs["kind"].reshape(rows, -1), kind_target, kind_active
    )
    kind = kind_loss / kind_count.clamp(min=1)
    entity_count = outputs["target_unit"].shape[1]
    indices = targets["selected_indices"].reshape(rows, -1)
    selected_mask = targets["selected_mask"].reshape(rows, -1)
    selected_count = selected_mask.sum(-1)
    selection_total = torch.zeros((), device=ability_logits.device)
    selection_active = torch.zeros((), dtype=torch.long, device=ability_logits.device)
    for step, logits in enumerate(outputs["selection"]):
        stop = torch.full((rows,), entity_count, dtype=torch.long, device=ability_logits.device)
        if step < indices.shape[1]:
            stop = torch.where(step < selected_count, indices[:, step], stop)
        active = (step <= selected_count) & step_active
        loss, count = categorical_loss(logits, stop, active)
        selection_total = selection_total + loss
        selection_active = selection_active + count
    selection = selection_total / selection_active.clamp(min=1)
    target_kind = targets["target_kind"].reshape(-1)
    kind_active = step_active
    kind_loss, kind_count = categorical_loss(outputs["target_kind"], target_kind, kind_active)
    target_kind_loss = kind_loss / kind_count.clamp(min=1)
    unit_active = (target_kind == TARGET_KINDS.index("unit")) & step_active
    unit_loss, unit_count = categorical_loss(
        outputs["target_unit"], targets["target_index"].reshape(-1), unit_active
    )
    target_unit_loss = unit_loss / unit_count.clamp(min=1)
    point_active = (target_kind == TARGET_KINDS.index("point")) & step_active
    map_size = targets["map_size"].reshape(rows, 2).clamp(min=1.0)
    point_target = targets["target_point"].reshape(rows, 2) / map_size
    point_losses = ((outputs["target_point"] - point_target) ** 2).mean(-1)
    point_count = point_active.sum()
    target_point_loss = (point_losses * point_active).sum() / point_count.clamp(min=1)
    queue_active = targets["queue_mask"].reshape(-1) & step_active
    queue_loss, queue_count = categorical_loss(
        outputs["queue"], targets["queue"].reshape(-1), queue_active
    )
    queue = queue_loss / queue_count.clamp(min=1)
    delay_active = targets["delay_mask"].reshape(-1) & step_active
    delay_target = torch.log1p(targets["delay"].reshape(-1).clamp(min=0))
    delay_losses = (outputs["delay"] - delay_target) ** 2
    delay_count = delay_active.sum()
    delay = (delay_losses * delay_active).sum() / delay_count.clamp(min=1)
    losses = {
        "ability": ability / ability_count.clamp(min=1),
        "kind": kind,
        "selection": selection,
        "target_kind": target_kind_loss,
        "target_unit": target_unit_loss,
        "target_point": target_point_loss,
        "queue": queue,
        "delay": delay,
    }
    total = sum(weights[name] * losses[name] for name in HEAD_NAMES)
    counts = {
        "ability": int(ability_count),
        "kind": int(kind_count),
        "selection": int(selection_active),
        "target_kind": int(kind_count),
        "target_unit": int(unit_count),
        "target_point": int(point_count),
        "queue": int(queue_count),
        "delay": int(delay_count),
    }
    losses["total"] = total
    losses["counts"] = counts
    return losses


def model_config_dict(config):
    return asdict(config)


def model_config_matches(stored, config):
    """Compare a checkpoint's model config, filling dataclass defaults for older runs."""
    if not isinstance(stored, dict):
        return False
    current = model_config_dict(config)
    if set(stored) - set(current):
        return False
    defaults = model_config_dict(BCModelConfig())
    return {key: stored.get(key, defaults[key]) for key in current} == current
