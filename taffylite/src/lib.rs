//! taffylite — a thin, fixed-size PyO3 binding of taffy 0.7 for saitenka's tooltip geometry.
//!
//! It exposes exactly what the `LayoutBackend` seam (`src/saitenka/render/layout_backend.py`) needs:
//! integer-exact box geometry from a flex tree whose leaf sizes are *already known*. Pillow measures
//! every row/glyph on the Python side before the backend is called, so — unlike the bake-off
//! prototype — there is **no measure callback and no Rust-side measure cache**. That machinery only
//! earns its keep for the deferred intra-block text-layout track (taffy driving wrap + calling back
//! into Pillow); it lives in the `experiment/layout-engine-bakeoff` worktree until that work lands.
//!
//! Why taffy at all (perf is a wash vs. the pure-Python default — both µs-scale, dominated by Pillow
//! raster): robustness. A mature CSS flexbox solver, not more hand-rolled arithmetic. See issue #146.
//!
//! Free-threading: `gil_used = false`. Every `Tree` is created, mutated, and dropped inside one call
//! with no shared state, so CPython does not re-enable the GIL on import.

use pyo3::prelude::*;
use taffy::prelude::*;
use taffy::style::{Display, FlexDirection, FlexWrap};
use taffy::{NodeId, Size as TSize};

/// A 4-tuple `(left, top, right, bottom)` of edge lengths (padding or margin), in px.
type Edges = (f32, f32, f32, f32);

fn edges_rect(e: Edges) -> taffy::geometry::Rect<LengthPercentage> {
    taffy::geometry::Rect {
        left: length(e.0),
        top: length(e.1),
        right: length(e.2),
        bottom: length(e.3),
    }
}

fn margin_rect(e: Edges) -> taffy::geometry::Rect<LengthPercentageAuto> {
    taffy::geometry::Rect {
        left: length(e.0),
        top: length(e.1),
        right: length(e.2),
        bottom: length(e.3),
    }
}

fn dim(v: Option<f32>) -> Dimension {
    v.map(length).unwrap_or_else(auto)
}

/// An imperative flex-tree builder over taffy. Handles are dense indices (0, 1, 2, … in creation
/// order); [`Tree::compute`] returns absolute rects index-aligned to those handles. Fixed-size only:
/// leaves carry an explicit `(w, h)`, so layout is a pure geometry solve with no callback into Python.
#[pyclass]
struct Tree {
    taffy: TaffyTree<()>,
    nodes: Vec<NodeId>,
    root: Option<NodeId>,
}

#[pymethods]
impl Tree {
    #[new]
    fn new() -> Self {
        Tree {
            taffy: TaffyTree::new(),
            nodes: Vec::new(),
            root: None,
        }
    }

    /// A fixed-size leaf `w`×`h` with optional `margin` `(l, t, r, b)`. Returns its handle.
    #[pyo3(signature = (width, height, margin=(0.0, 0.0, 0.0, 0.0)))]
    fn add_leaf(&mut self, width: f32, height: f32, margin: Edges) -> usize {
        let style = Style {
            size: TSize {
                width: length(width),
                height: length(height),
            },
            margin: margin_rect(margin),
            ..Default::default()
        };
        let id = self.taffy.new_leaf(style).unwrap();
        self.push(id)
    }

    /// A flex container. `direction` is `"row"` or `"column"`; `gap` is the main-axis gap between
    /// children; `padding`/`margin` are `(l, t, r, b)`; `width`/`height` fix the box (else auto).
    #[pyo3(signature = (children, direction="column", gap=0.0, padding=(0.0, 0.0, 0.0, 0.0),
                        margin=(0.0, 0.0, 0.0, 0.0), width=None, height=None, wrap=false))]
    #[allow(clippy::too_many_arguments)]
    fn add_flex(
        &mut self,
        children: Vec<usize>,
        direction: &str,
        gap: f32,
        padding: Edges,
        margin: Edges,
        width: Option<f32>,
        height: Option<f32>,
        wrap: bool,
    ) -> usize {
        let kids: Vec<NodeId> = children.iter().map(|&h| self.nodes[h]).collect();
        let style = Style {
            display: Display::Flex,
            flex_direction: if direction == "row" {
                FlexDirection::Row
            } else {
                FlexDirection::Column
            },
            flex_wrap: if wrap { FlexWrap::Wrap } else { FlexWrap::NoWrap },
            gap: TSize {
                width: length(gap),
                height: length(gap),
            },
            padding: edges_rect(padding),
            margin: margin_rect(margin),
            size: TSize {
                width: dim(width),
                height: dim(height),
            },
            ..Default::default()
        };
        let id = self.taffy.new_with_children(style, &kids).unwrap();
        self.push(id)
    }

    /// Mark a handle as the root laid out by [`Tree::compute`].
    fn set_root(&mut self, handle: usize) {
        self.root = Some(self.nodes[handle]);
    }

    /// Solve the tree and return absolute (content-space) rects `[(x, y, w, h)]`, index-aligned to
    /// handles. `available_width` bounds the root's cross size (`None` = MaxContent).
    #[pyo3(signature = (available_width=None))]
    fn compute(&mut self, available_width: Option<f32>) -> Vec<(f32, f32, f32, f32)> {
        let root = self.root.expect("set_root not called");
        let avail = TSize {
            width: available_width
                .map(AvailableSpace::Definite)
                .unwrap_or(AvailableSpace::MaxContent),
            height: AvailableSpace::MaxContent,
        };
        self.taffy.compute_layout(root, avail).unwrap();
        absolute_rects(&self.taffy, root, &self.nodes)
    }
}

impl Tree {
    fn push(&mut self, id: NodeId) -> usize {
        self.nodes.push(id);
        self.nodes.len() - 1
    }
}

/// Walk from `root`, converting taffy's parent-relative locations to absolute content-space rects,
/// returned index-aligned to `nodes` (handle order).
fn absolute_rects(
    taffy: &TaffyTree<()>,
    root: NodeId,
    nodes: &[NodeId],
) -> Vec<(f32, f32, f32, f32)> {
    use std::collections::HashMap;
    let mut abs: HashMap<NodeId, (f32, f32, f32, f32)> = HashMap::new();
    let mut stack = vec![(root, 0.0f32, 0.0f32)];
    while let Some((node, ox, oy)) = stack.pop() {
        let l = taffy.layout(node).unwrap();
        let (x, y) = (ox + l.location.x, oy + l.location.y);
        abs.insert(node, (x, y, l.size.width, l.size.height));
        for child in taffy.children(node).unwrap() {
            stack.push((child, x, y));
        }
    }
    nodes.iter().map(|id| abs[id]).collect()
}

/// Row-stack geometry — the `LayoutBackend.cumulative` primitive, computed by taffy's flexbox solver.
///
/// A `flex-direction: column` of fixed-height rows inside `top_pad` top padding, each row carrying a
/// trailing `margin-bottom` of `gaps[i]` (the last row's gap is dropped, matching `compose_panel`).
/// Returns integer `(starts, ends)` — row tops and bottoms in content space. Integer-exact for
/// integer inputs: cumulative sums stay within f32's exact-integer range (< 2^24) and taffy rounds to
/// whole px, so a `.round()` is a no-op that only guards against a stray ULP.
#[pyfunction]
fn column(heights: Vec<f32>, gaps: Vec<f32>, top_pad: f32) -> (Vec<i64>, Vec<i64>) {
    let n = heights.len();
    if n == 0 {
        return (Vec::new(), Vec::new());
    }
    let mut taffy: TaffyTree<()> = TaffyTree::new();
    let mut kids = Vec::with_capacity(n);
    for (i, &h) in heights.iter().enumerate() {
        let mb = if i + 1 < n && i < gaps.len() {
            gaps[i]
        } else {
            0.0
        };
        let style = Style {
            size: TSize {
                width: auto(),
                height: length(h),
            },
            margin: taffy::geometry::Rect {
                left: length(0.0_f32),
                top: length(0.0_f32),
                right: length(0.0_f32),
                bottom: length(mb),
            },
            ..Default::default()
        };
        kids.push(taffy.new_leaf(style).unwrap());
    }
    let root_style = Style {
        display: Display::Flex,
        flex_direction: FlexDirection::Column,
        padding: taffy::geometry::Rect {
            left: length(0.0_f32),
            top: length(top_pad),
            right: length(0.0_f32),
            bottom: length(0.0_f32),
        },
        ..Default::default()
    };
    let root = taffy.new_with_children(root_style, &kids).unwrap();
    taffy
        .compute_layout(
            root,
            TSize {
                width: AvailableSpace::MaxContent,
                height: AvailableSpace::MaxContent,
            },
        )
        .unwrap();
    let mut starts = Vec::with_capacity(n);
    let mut ends = Vec::with_capacity(n);
    for k in kids {
        let l = taffy.layout(k).unwrap();
        let y = l.location.y.round() as i64;
        starts.push(y);
        ends.push(y + l.size.height.round() as i64);
    }
    (starts, ends)
}

#[pymodule(gil_used = false)]
fn taffylite(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Tree>()?;
    m.add_function(wrap_pyfunction!(column, m)?)?;
    Ok(())
}
