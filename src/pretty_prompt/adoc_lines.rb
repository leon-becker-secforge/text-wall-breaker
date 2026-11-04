# AsciiDoc on stdin -> HTML on stdout, each block carrying `data-first` (its source line).
#
# Run by render.py with `ruby`. Asciidoctor's CLI cannot report source lines; its API can
# (`sourcemap`). Safe mode is `secure`: no include:: and no reading of other files.
require 'asciidoctor'
require 'asciidoctor/converter/html5'

MARKED = %i[paragraph listing literal quote verse admonition example sidebar open ulist olist
            dlist colist table image thematic_break stem pass].freeze

class LineConverter < Asciidoctor::Converter::Html5Converter
  Asciidoctor::Converter.register self, 'html5'

  def convert(node, transform = node.node_name, opts = nil)
    out = super
    return out unless out.is_a?(String)
    loc = node.respond_to?(:source_location) ? node.source_location : nil
    return out unless loc
    case node.context
    when :section
      # The heading, not the whole section: a click on it must not select every block below.
      out = out.sub(/<h[1-6]/) { |tag| %(#{tag} data-first="#{loc.lineno}") }
    when *MARKED
      out = out.sub(/\A(\s*<[a-zA-Z0-9]+)/) { %(#{$1} data-first="#{loc.lineno}") }
    end
    if %i[ulist olist].include?(node.context)
      index = 0
      out = out.gsub('<li>') do
        item = node.items[index]
        index += 1
        item && item.source_location ? %(<li data-first="#{item.source_location.lineno}">) : '<li>'
      end
    end
    out
  end
end

source = $stdin.read
html = Asciidoctor.convert(
  source,
  safe: :secure,
  sourcemap: true,
  standalone: false,
  backend: 'html5',
  attributes: { 'showtitle' => '', 'nofooter' => '' }
)
$stdout.write(html)
