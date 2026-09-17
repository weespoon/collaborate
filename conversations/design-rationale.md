# Design rationale

## Why a doodle

I wanted something self-contained that mirrored my own curiosity rather than a
demo of a capability I already believed in.

The thing I keep circling back to is how machines came to see. ImageNet gave
the field a corpus big enough to matter and a benchmark honest enough to chase,
and once deep networks had both, recognition stopped being hand-engineered. The
same lesson — scale the data, scale the model, let the representation be
learned — moved to language with the transformer, and BERT showed that
pre-training on a very large corpus produced something general rather than
task-specific. Scaling did the rest, and that is the world we now work in.

Language is close to the center of what it means to be human, which is why text
was the obvious interface. But so is doodling. Any bored student knows that.
Drawing is a way of thinking that runs alongside language rather than beneath
it, and it is one of the first things we do with a shared piece of paper.

The inception was literally that image: passing a sheet back and forth with
Claude. One of us adds a mark, the other answers it. No prompt box, no chat log
— just the page and whose turn it is.

## What the interface is arguing

LLMs can solve genuinely hard problems. That is settled. What is not settled is
how we interact with them. We have pictures, recordings, and the typed word, and
each one arrived with its own conventions that had to be invented. A sketch that
gets answered is a small argument that the set is not closed yet.

The turn takes twenty to thirty seconds, which is far too long to stare at a
spinner. So the model's reasoning streams into the page while it draws. That was
not a debugging affordance that survived into the product — it is the product.
Watching a collaborator think is most of what makes the exchange feel like an
exchange.

## Decisions I made, and why

**It had to be deployable.** Not a notebook, not a local script. A URL I can
send to someone. That ruled out designs that assume a laptop is running.

**The key and the prompts live on the server.** The browser sends an SVG and
receives an SVG. It never sees a key, and it cannot choose a model, a token
budget, or a prompt. This is partly hygiene and partly economics: an endpoint
that takes arbitrary instructions is an endpoint that can run up an arbitrary
bill. A turn costs about four cents, and I wanted to keep it that way.

**The scope is deliberately tiny.** Draw, send, undo. Undo walks all the way
back through both players' turns. There is no account, no gallery, no sharing,
no persistence beyond the browser's local storage. Every feature I did not build
is a feature I did not have to debug in the time I had.

**The time constraint was the real designer.** One to two hours. That budget
chose the SVG-in, SVG-out contract over anything cleverer, chose a stateless
backend over storage, and chose one prompt file over a prompt system. Most of
what makes this coherent is what the clock refused to let me add.

## What I would do with more time

Pencil-and-paper games. Sprouts is the one I want to try first — simple rules,
a real win condition, and a board that is a drawing rather than a grid. Dots and
boxes and exquisite corpse are the same architecture with different rules, which
is why prompts are versioned files keyed by id rather than a string in the
source. The machinery is already shaped for it.

## What it actually cost

- **A few minutes a day** thinking about it, over the last few days.
- **15 minutes** prototyping against a local SVG editor, by hand, to find out
  whether the model would reproduce a document faithfully and add to it rather
  than redraw it. It would. That single result is what made the rest worth
  building.
- **~2 hours** coding — some of it distracted, finishing the workday, and some
  of it lost to playing with the tool instead of building it. The second kind of
  distraction is the better sign.
- **~20 minutes** on the writeup and video.

The prototype came before the code, and the prompt that came out of those 15
minutes is the one running in production, unchanged.
